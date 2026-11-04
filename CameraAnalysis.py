import cv2
import time
import base64
import json
import sys
import os
import threading
from openai import OpenAI


# Add / change camera streams here — each runs in its own thread
CAMERAS = [
    {
        "id": "cam1",
        "name": "Camera 1",
        "url": "http://192.168.5.148:8080/video",
    },
    {
        "id": "cam2",
        "name": "Camera 2",
        "url": "http://192.168.5.133:8080/video",  # <- set your second stream URL
    },
]

GROK_API_KEY = "xai-" # input real api-key here
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COOLDOWN = 10
MOTION_THRESHOLD = 5000

# Protects shared stdout so log lines from both cameras don't interleave mid-line
print_lock = threading.Lock()

client = OpenAI(
    api_key=GROK_API_KEY,
    base_url="https://api.x.ai/v1",
)


def log(camera_name, message):
    with print_lock:
        print(f"[{camera_name}] {message}")


def encode_frame(frame):
    """Resizes and encodes the frame to base64 for the API."""
    resized_frame = cv2.resize(frame, (480, 360))
    _, buffer = cv2.imencode(".jpg", resized_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
    return base64.b64encode(buffer).decode("utf-8")


def ask_grok_with_baseline(baseline_b64, current_b64):
    prompt = """You are a security agent. 
You are provided with two images:
1. Reference image (Normal state / Baseline)
2. Current surveillance image

Compare the current image exactly with the reference image.
Determine if there are any deviations (e.g., unauthorized persons, missing or newly added objects, open doors/moved chairs) with appropriate detail in description.

Respond EXCLUSIVELY in the following JSON format without any additional text:
{
  "deviation_detected": true,
  "changes_detected": ["Person at the desk", "Chair moved"],
  "reasoning": "A person is in the room who was not present in the reference image.",
  "horizontal_position": "LEFT"
}

Rules:
- If deviation_detected is false, set changes_detected to [] and horizontal_position to "NONE".
- Do NOT invent room zones, desk labels, or named areas — only report what changed and where it sits in the frame.
- horizontal_position MUST be the last field. It describes where the main difference appears in the current image frame:
  - "FAR LEFT"   = change is mainly in the left fifth of the image or being cut off by the left edge of the image
  - "LEFT"   = change is mainly in the left third of the image
  - "MIDDLE" = change is mainly in the center third of the image
  - "RIGHT"  = change is mainly in the right third of the image
  - "FAR RIGHT"  = change is mainly in the right fifth of the image or being cut of by the right edge of the image
  - "NONE"   = no deviation detected
- If multiple changes span several areas, pick the side where the most significant / largest change is.
"""

    try:
        response = client.chat.completions.create(
            model="grok-4.5",
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "text", "text": "=== IMAGE 1: BASELINE (NORMAL STATE) ==="},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{baseline_b64}"},
                        },
                        {"type": "text", "text": "=== IMAGE 2: CURRENT IMAGE (ALARM) ==="},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{current_b64}"},
                        },
                    ],
                }
            ],
        )
        return json.loads(response.choices[0].message.content)

    except Exception as e:
        return {
            "deviation_detected": True,
            "changes_detected": ["API_CALL_FAILED"],
            "reasoning": f"Grok evaluation failed ({e}). Manual operator verification required.",
            "horizontal_position": "NONE",
        }


def run_grok_async(camera_name, baseline_img, current_img):
    """Executes API call in the background without freezing the video stream."""
    log(camera_name, "[GROK] Comparing current image with baseline...")
    decision = ask_grok_with_baseline(baseline_img, current_img)

    with print_lock:
        print(f"\n================ DEVIATION ANALYSIS ({camera_name}) ================")
        print(json.dumps(decision, indent=2, ensure_ascii=False))
        print("====================================================")

    if decision.get("deviation_detected"):
        pass


def monitor_camera(camera, stop_event):
    """
    Full capture / motion / alarm loop for one camera.
    Designed to run as a dedicated thread so cameras never block each other.
    """
    cam_id = camera["id"]
    name = camera["name"]
    url = camera["url"]

    cap = cv2.VideoCapture(url)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not cap.isOpened():
        log(name, f"ERROR: Could not open stream: {url}")
        return

    try:
        log(name, "Calibrating baseline... keep the room clear for 2 seconds.")
        time.sleep(2)

        ret, baseline_frame = cap.read()
        if not ret:
            log(name, "ERROR: Could not capture baseline.")
            return

        baseline_path = os.path.join(SCRIPT_DIR, f"reference_baseline_{cam_id}.jpg")
        cv2.imwrite(baseline_path, baseline_frame)
        baseline_img = encode_frame(baseline_frame)
        log(name, f"Baseline saved: {baseline_path}")

        bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=50, detectShadows=False
        )
        log(name, "Warming up background subtractor (3 seconds)...")
        start_time = time.time()
        while time.time() - start_time < 3 and not stop_event.is_set():
            ret, frame = cap.read()
            if ret:
                resized_frame = cv2.resize(frame, (640, 480))
                blur_frame = cv2.GaussianBlur(resized_frame, (5, 5), 0)
                bg_subtractor.apply(blur_frame)

        log(name, "Monitoring active. Waiting for motion...")
        last_alarm_time = 0

        while not stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                continue

            resized_frame = cv2.resize(frame, (640, 480))
            blur_frame = cv2.GaussianBlur(resized_frame, (5, 5), 0)

            fg_mask = bg_subtractor.apply(blur_frame)
            _, fg_mask = cv2.threshold(fg_mask, 254, 255, cv2.THRESH_BINARY)
            fg_mask = cv2.erode(fg_mask, None, iterations=1)
            fg_mask = cv2.dilate(fg_mask, None, iterations=2)

            if time.time() - last_alarm_time < COOLDOWN:
                time.sleep(0.03)
                continue

            motion_score = cv2.countNonZero(fg_mask)
            if motion_score > MOTION_THRESHOLD:
                log(name, f"ALARM — motion detected (score: {motion_score})")

                # Grab a slightly fresher frame after motion settles
                delay_start = time.time()
                action_frame = frame
                while time.time() - delay_start < 0.8 and not stop_event.is_set():
                    ret_d, fresh_frame = cap.read()
                    if ret_d:
                        action_frame = fresh_frame
                        resized_d = cv2.resize(action_frame, (640, 480))
                        blur_d = cv2.GaussianBlur(resized_d, (5, 5), 0)
                        bg_subtractor.apply(blur_d)

                change_path = os.path.join(SCRIPT_DIR, f"latest_change_{cam_id}.jpg")
                cv2.imwrite(change_path, action_frame)

                comparison_img = cv2.hconcat([baseline_frame, action_frame])
                comparison_path = os.path.join(SCRIPT_DIR, f"baseline_vs_change_{cam_id}.jpg")
                cv2.imwrite(comparison_path, comparison_img)

                current_img = encode_frame(action_frame)

                # Nested async call: Grok never blocks either camera thread
                threading.Thread(
                    target=run_grok_async,
                    args=(name, baseline_img, current_img),
                    daemon=True,
                    name=f"grok-{cam_id}",
                ).start()

                last_alarm_time = time.time()

            time.sleep(0.03)

    except Exception as e:
        log(name, f"ERROR in monitor loop: {e}")
    finally:
        if cap.isOpened():
            cap.release()
        log(name, "Camera released.")


if __name__ == "__main__":
    stop_event = threading.Event()
    threads = []

    print(f"[SYSTEM] Starting {len(CAMERAS)} camera thread(s)...")
    for camera in CAMERAS:
        t = threading.Thread(
            target=monitor_camera,
            args=(camera, stop_event),
            daemon=True,
            name=camera["id"],
        )
        t.start()
        threads.append(t)

    try:
        # Keep main thread alive so Ctrl+C works; worker threads do the real work
        while any(t.is_alive() for t in threads):
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[SYSTEM] Manual stop (Ctrl+C). Shutting down...")
        stop_event.set()
        for t in threads:
            t.join(timeout=5)
        print("[SYSTEM] All cameras stopped. Exiting.")
        sys.exit(0)

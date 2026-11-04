import cv2, time, base64, json, sys, os, threading, requests
from openai import OpenAI

CAMERAS = [
    {"id": "cam1", "name": "Camera 1", "url": "http://192.168.5.148:8080/video"},
    {"id": "cam2", "name": "Camera 2", "url": "http://192.168.5.133:8080/video"},
]

GROK_API_KEY = os.environ.get("GROK_API_KEY")
WEBHOOK_URL = "http://localhost:5000/webhook"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COOLDOWN = 10
MOTION_THRESHOLD = 5000

print_lock = threading.Lock()
client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.x.ai/v1")

def log(camera_name, message):
    with print_lock:
        print(f"[{camera_name}] {message}")

def send(camera_name, payload):
    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=5)
        log(camera_name, f"[WEBHOOK] {r.json()}")
    except Exception as e:
        log(camera_name, f"[WEBHOOK] failed: {e}")

def encode_frame(frame):
    resized = cv2.resize(frame, (480, 360))
    _, buf = cv2.imencode(".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
    return base64.b64encode(buf).decode("utf-8")

PROMPT = """You are a security agent.
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
- Do NOT invent room zones, desk labels, or named areas - only report what changed and where it sits in the frame.
- horizontal_position MUST be the last field. It describes where the main difference appears in the current image frame:
  - "FAR LEFT"  = change is mainly in the left fifth of the image or cut off by the left edge
  - "LEFT"      = change is mainly in the left third of the image
  - "MIDDLE"    = change is mainly in the center third of the image
  - "RIGHT"     = change is mainly in the right third of the image
  - "FAR RIGHT" = change is mainly in the right fifth of the image or cut off by the right edge
  - "NONE"      = no deviation detected
- If multiple changes span several areas, pick the side where the most significant / largest change is.
"""

def ask_grok_with_baseline(baseline_b64, current_b64):
    try:
        response = client.chat.completions.create(
            model="grok-4.5",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "text", "text": "=== IMAGE 1: BASELINE (NORMAL STATE) ==="},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{baseline_b64}"}},
                {"type": "text", "text": "=== IMAGE 2: CURRENT IMAGE (ALARM) ==="},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{current_b64}"}},
            ]}],
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        return {"deviation_detected": True, "changes_detected": ["API_CALL_FAILED"],
                "reasoning": f"Grok evaluation failed ({e}). Manual operator verification required.",
                "horizontal_position": "NONE"}

def run_grok_async(camera_name, cam_id, baseline_img, current_img, motion_score):
    log(camera_name, "[GROK] Comparing current image with baseline...")
    t0 = time.time()
    decision = ask_grok_with_baseline(baseline_img, current_img)
    grok_ms = int((time.time() - t0) * 1000)
    with print_lock:
        print(f"\n===== DEVIATION ANALYSIS ({camera_name}) {grok_ms} ms =====")
        print(json.dumps(decision, indent=2, ensure_ascii=False))
    payload = dict(decision)
    payload.update(camera_id=cam_id, motion_score=motion_score, grok_ms=grok_ms)
    if decision.get("deviation_detected"):
        payload["photo"] = current_img
    send(camera_name, payload)

def prep(frame):
    return cv2.GaussianBlur(cv2.resize(frame, (640, 480)), (5, 5), 0)

def monitor_camera(camera, stop_event):
    cam_id, name, url = camera["id"], camera["name"], camera["url"]
    cap = cv2.VideoCapture(url)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not cap.isOpened():
        log(name, f"ERROR: Could not open stream: {url}")
        send(name, {"camera_id": cam_id, "status": "camera_error"})
        return
    try:
        log(name, "Calibrating baseline... keep the room clear for 2 seconds.")
        time.sleep(2)
        ret, baseline_frame = cap.read()
        if not ret:
            log(name, "ERROR: Could not capture baseline.")
            send(name, {"camera_id": cam_id, "status": "camera_error"})
            return
        cv2.imwrite(os.path.join(SCRIPT_DIR, f"reference_baseline_{cam_id}.jpg"), baseline_frame)
        baseline_img = encode_frame(baseline_frame)

        bg = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=50, detectShadows=False)
        log(name, "Warming up background subtractor (3 seconds)...")
        start = time.time()
        while time.time() - start < 3 and not stop_event.is_set():
            ret, frame = cap.read()
            if ret: bg.apply(prep(frame))

        log(name, "Monitoring active. Waiting for motion...")
        send(name, {"camera_id": cam_id, "status": "camera_online"})
        last_alarm = 0

        while not stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                continue
            mask = bg.apply(prep(frame))
            _, mask = cv2.threshold(mask, 254, 255, cv2.THRESH_BINARY)
            mask = cv2.dilate(cv2.erode(mask, None, iterations=1), None, iterations=2)

            if time.time() - last_alarm < COOLDOWN:
                time.sleep(0.03); continue

            motion_score = cv2.countNonZero(mask)
            if motion_score > MOTION_THRESHOLD:
                log(name, f"ALARM - motion detected (score: {motion_score})")
                action = frame
                t = time.time()
                while time.time() - t < 0.8 and not stop_event.is_set():
                    r2, f2 = cap.read()
                    if r2:
                        action = f2; bg.apply(prep(f2))
                cv2.imwrite(os.path.join(SCRIPT_DIR, f"latest_change_{cam_id}.jpg"), action)
                threading.Thread(target=run_grok_async,
                                 args=(name, cam_id, baseline_img, encode_frame(action), motion_score),
                                 daemon=True).start()
                last_alarm = time.time()
            time.sleep(0.03)
    except Exception as e:
        log(name, f"ERROR in monitor loop: {e}")
    finally:
        cap.release()
        log(name, "Camera released.")

if __name__ == "__main__":
    if not GROK_API_KEY:
        print("Set GROK_API_KEY first:  $env:GROK_API_KEY = \"xai-...\""); sys.exit(1)
    stop_event = threading.Event()
    threads = [threading.Thread(target=monitor_camera, args=(c, stop_event), daemon=True) for c in CAMERAS]
    print(f"[SYSTEM] Starting {len(threads)} camera thread(s)...")
    for t in threads: t.start()
    try:
        while any(t.is_alive() for t in threads): time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[SYSTEM] Stopping..."); stop_event.set()
        for t in threads: t.join(timeout=5)

import cv2
import time
import base64
import json
import sys
import os
from openai import OpenAI
import threading
import json


STREAM_URL = "http://10.204.10.250:8080/video"
GROK_API_KEY = "xai-"  # Insert your real xai-... key here
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


client = OpenAI(
    api_key=GROK_API_KEY,
    base_url="https://api.x.ai/v1"
)
  
def encode_frame(frame):
    """Resizes and encodes the frame to base64 for the API."""
    resized_frame = cv2.resize(frame, (480, 360))
    _, buffer = cv2.imencode('.jpg', resized_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 60])    
    return base64.b64encode(buffer).decode('utf-8')

def ask_grok_with_baseline(baseline_b64, current_b64):
    print("[GROK] Comparing current image with baseline...")
    
    prompt = """You are a security agent. 
You are provided with two images:
1. Reference image (Normal state / Baseline)
2. Current surveillance image

Compare the current image exactly with the reference image.
Determine if there are any deviations (e.g., unauthorized persons, missing or newly added objects, open doors/moved chairs).

Respond EXCLUSIVELY in the following JSON format without any additional text:
{
  "deviation_detected": true,
  "target_zone": "ZONE_B_DESK",
  "changes_detected": ["Person at the desk", "Chair moved"],
  "reasoning": "A person is in the room who was not present in the reference image."
}

Rules:
- target_zone is "ZONE_A_DOOR", "ZONE_B_DESK" or "NONE".
- If deviation_detected is false, set target_zone to "NONE" and changes_detected to [].
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
                            "image_url": {"url": f"data:image/jpeg;base64,{baseline_b64}"}
                        },
                        {"type": "text", "text": "=== IMAGE 2: CURRENT IMAGE (ALARM) ==="},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{current_b64}"}
                        }
                    ]
                }
            ]
        )

        return json.loads(response.choices[0].message.content)

    except Exception as e:
        print(f"[GROK ERROR] API call failed: {e}")
        # Fail-safe: force human alert if AI API fails
        return {
            "deviation_detected": True,
            "target_zone": "CHECK_REQUIRED",
            "changes_detected": ["API_CALL_FAILED"],
            "reasoning": f"Grok evaluation failed ({e}). Manual operator verification required."
        }

def run_grok_async(baseline_img, current_img):
    """Executes API call in the background without freezing the video stream."""
    decision = ask_grok_with_baseline(baseline_img, current_img)
    
    print("\n================ DEVIATION ANALYSIS ================")
    print(json.dumps(decision, indent=2, ensure_ascii=False))
    print("====================================================")
    
    if decision.get("deviation_detected") and decision.get("target_zone") != "NONE":
        pass


if __name__ == "__main__":
    cap = cv2.VideoCapture(STREAM_URL)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not cap.isOpened():
        raise RuntimeError("Could not open camera stream.")

    try:
        # 1. Capture baseline
        print("[BASELINE] Calibrating normal state... Please keep the room clear for 2 seconds.")
        time.sleep(2)
        ret, baseline_frame = cap.read()
        if not ret:
            raise RuntimeError("Could not capture baseline.")
        
        # Overwrite local baseline image file
        baseline_path = os.path.join(SCRIPT_DIR, "reference_baseline.jpg")
        cv2.imwrite(baseline_path, baseline_frame)
        print(f"[BASELINE] Reference image saved to: {baseline_path}")
        
        baseline_img = encode_frame(baseline_frame)
        print("[BASELINE] Reference image successfully saved to disk (reference_baseline.jpg).")

       # 2. Warm up background subtractor (FIXED: Added Blur and Resize)
        bg_subtractor = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=50, detectShadows=False)
        print("[STREAM] Warming up (3 seconds)...")
        start_time = time.time()
        while time.time() - start_time < 3:
            ret, frame = cap.read()
            if ret:
                # Resize and blur during warm-up to match main loop
                resized_frame = cv2.resize(frame, (640, 480))
                blur_frame = cv2.GaussianBlur(resized_frame, (5, 5), 0)
                bg_subtractor.apply(blur_frame)

        print("[STREAM] Monitoring active. Waiting for motion... (Press Ctrl+C to stop)")
        
        last_alarm_time = 0
        COOLDOWN = 10

        # 3. Continuous monitoring loop
        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            # Resize to normalize the 5000-pixel threshold and reduce CPU load
            resized_frame = cv2.resize(frame, (640, 480))
            blur_frame = cv2.GaussianBlur(resized_frame, (5, 5), 0)

            fg_mask = bg_subtractor.apply(blur_frame)
            _, fg_mask = cv2.threshold(fg_mask, 254, 255, cv2.THRESH_BINARY)
            
            fg_mask = cv2.erode(fg_mask, None, iterations=1)
            fg_mask = cv2.dilate(fg_mask, None, iterations=2)

            if time.time() - last_alarm_time < COOLDOWN:
                continue

            motion_score = cv2.countNonZero(fg_mask)

            if motion_score > 5000:
                print(f"[ALARM] Motion detected! (Score: {motion_score})")

                delay_start = time.time()
                action_frame = frame
                while time.time() - delay_start < 0.8:
                    ret_d, fresh_frame = cap.read()
                    if ret_d:
                        action_frame = fresh_frame
                        resized_d = cv2.resize(action_frame, (640, 480))
                        blur_d = cv2.GaussianBlur(resized_d, (5, 5), 0)
                        bg_subtractor.apply(blur_d)

                # Overwrite only the current change image
                change_path = os.path.join(SCRIPT_DIR, "latest_change.jpg")
                cv2.imwrite(change_path, action_frame)
                
                # Create and overwrite a side-by-side combined image (Left: Baseline | Right: Change)
                comparison_img = cv2.hconcat([baseline_frame, action_frame])
                comparison_path = os.path.join(SCRIPT_DIR, "baseline_vs_change.jpg")
                cv2.imwrite(comparison_path, comparison_img)
                
                current_img = encode_frame(action_frame)
                
                # Dispatch Grok API call to background thread (non-blocking)
                thread = threading.Thread(
                    target=run_grok_async, 
                    args=(baseline_img, current_img), 
                    daemon=True
                )
                thread.start()
                
                last_alarm_time = time.time()

            time.sleep(0.03)
    except KeyboardInterrupt:
        print("\n[SYSTEM] Manual stop triggered (Ctrl+C). Shutting down gracefully...")
        
    finally:
        # Guarantee camera release even if an error occurs
        if 'cap' in locals() and cap.isOpened():
            cap.release()
        print("[SYSTEM] Camera released. Exiting.")
        sys.exit(0)

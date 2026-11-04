import json, os, base64, uuid, threading, heapq, itertools
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
os.makedirs("logs", exist_ok=True)
os.makedirs("frames", exist_ok=True)

PRIORITY = {"high": 3, "medium": 2, "low": 1}
MANUAL_PRIORITY = 4

lock = threading.Lock()
state = {"drone_busy": False, "mission": None}
pending = {}
queue = []                 # heap: (-priority, order, task)
counter = itertools.count()

def log_event(event, **data):
    row = {"time": datetime.now().isoformat(), "event": event, **data}
    with open("logs/events.json", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(row)

def start_mission(task):
    state["drone_busy"] = True
    state["mission"] = task
    log_event("drone_dispatched", task_id=task["task_id"], target_zone=task["target_zone"],
              camera_id=task.get("camera_id"), priority=task["priority"])
    # SEND DRONE COMMAND HERE

def dispatch_or_queue(task):
    if not state["drone_busy"]:
        start_mission(task)
        return "dispatched"
    heapq.heappush(queue, (-task["priority"], next(counter), task))
    log_event("task_queued", task_id=task["task_id"], target_zone=task["target_zone"],
              priority=task["priority"], queue_size=len(queue))
    return "queued"

@app.get("/")
def index():
    return send_from_directory(".", "index.html")

@app.get("/frames/<name>")
def frames(name):
    return send_from_directory("frames", name)

@app.post("/webhook")
def webhook():
    d = request.get_json(force=True)
    cam = d.get("camera_id", "unknown")
    zone = d.get("target_zone")
    sev = d.get("severity", "medium")
    prio = PRIORITY.get(sev, 2)

    photo = None
    if d.get("photo"):
        photo = f"{uuid.uuid4().hex[:8]}.jpg"
        with open(f"frames/{photo}", "wb") as f:
            f.write(base64.b64decode(d["photo"]))

    log_event("camera_input", camera_id=cam, target_zone=zone, severity=sev,
              deviation_detected=d.get("deviation_detected"), photo=photo)

    if not d.get("deviation_detected"):
        return jsonify(status="none")

    with lock:
        m = state["mission"]
        if state["drone_busy"] and m and m["target_zone"] == zone:
            log_event("ignored_own_drone", camera_id=cam, target_zone=zone)
            return jsonify(status="ignored_own_drone")

        tid = uuid.uuid4().hex[:8]
        pending[tid] = {"task_id": tid, "camera_id": cam, "target_zone": zone,
                        "severity": sev, "priority": prio,
                        "changes_detected": d.get("changes_detected"),
                        "reasoning": d.get("reasoning"), "photo": photo,
                        "order": next(counter), "time": datetime.now().isoformat()}
        log_event("awaiting_decision", task_id=tid, camera_id=cam, target_zone=zone,
                  severity=sev, priority=prio, photo=photo)
    return jsonify(status="awaiting_decision", task_id=tid, priority=prio)

@app.get("/pending")
def get_pending():
    with lock:
        items = sorted(pending.values(), key=lambda t: (-t["priority"], t["order"]))
    return jsonify(items)

@app.post("/decision")
def decision():
    d = request.get_json(force=True)
    with lock:
        task = pending.pop(d.get("task_id"), None)
        if not task:
            return jsonify(error="not found"), 404
        log_event("final_decision", task_id=task["task_id"], target_zone=task["target_zone"],
                  camera_id=task["camera_id"], priority=task["priority"], decision=d.get("decision"))
        if d.get("decision") != "yes":
            return jsonify(status="rejected")
        return jsonify(status=dispatch_or_queue(task))

@app.post("/command")
def command():
    d = request.get_json(force=True)
    with lock:
        task = {"task_id": uuid.uuid4().hex[:8], "camera_id": "operator",
                "target_zone": d.get("target_zone"), "priority": MANUAL_PRIORITY}
        log_event("manual_command", task_id=task["task_id"], target_zone=task["target_zone"])
        return jsonify(status=dispatch_or_queue(task))

@app.post("/drone/arrived")
def arrived():
    with lock:
        m = state["mission"]
        log_event("mission_completed", task_id=m["task_id"] if m else None,
                  target_zone=m["target_zone"] if m else None)
        state["drone_busy"], state["mission"] = False, None
        if queue:
            _, _, task = heapq.heappop(queue)
            start_mission(task)
            return jsonify(status="next_mission", target_zone=task["target_zone"])
        log_event("drone_idle")
    return jsonify(status="idle")

@app.get("/status")
def status():
    with lock:
        q = [t for _, _, t in sorted(queue)]
        return jsonify(drone_busy=state["drone_busy"], mission=state["mission"],
                       queue=[{"zone": t["target_zone"], "priority": t["priority"]} for t in q],
                       pending=len(pending))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

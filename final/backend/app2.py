from flask import Flask, request, jsonify
from flask_cors import CORS
from collections import defaultdict
from datetime import datetime, timedelta
import heapq

app = Flask(__name__)
CORS(app)

# =====================================================
# BST IMPLEMENTATION (LOAD SHEDDING)
# =====================================================
class TreeNode:
    def __init__(self, key, area_id):
        self.key = key
        self.area_id = area_id
        self.left = None
        self.right = None

class AreaBST:
    def __init__(self):
        self.root = None

    def insert(self, key, area_id):
        def _insert(node, key, area_id):
            if not node:
                return TreeNode(key, area_id)
            if key < node.key:
                node.left = _insert(node.left, key, area_id)
            else:
                node.right = _insert(node.right, key, area_id)
            return node
        self.root = _insert(self.root, key, area_id)

    def inorder(self):
        result = []
        def _inorder(node):
            if node:
                _inorder(node.left)
                result.append(node.area_id)
                _inorder(node.right)
        _inorder(self.root)
        return result

    def rebuild(self, areas):
        self.root = None
        for aid, area in areas.items():
            # LOW PRIORITY FIRST (P4 → P1)
            key = (-area["priority"], -area["load_kw"], aid)
            self.insert(key, aid)

# =====================================================
# APPLICATION STATE
# =====================================================
class AppState:
    def __init__(self):
        self.substations = {1: {"id": 1, "name": "Main Substation"}}

        self.feeders = {}
        self.next_feeder_id = 1

        self.areas = {}
        self.next_area_id = 1

        # Load shedding
        self.schedule = []
        self.P_avail_hour = 0
        self.area_cut_hours = {}
        self.area_last_cut_slot = {}
        self.area_tree = AreaBST()

        # Maintenance (priority queue – correct)
        self.maintenance_pq = []          # (priority, timestamp, task_id)
        self.maintenance_map = {}         # task_id → task
        self.resolved_tasks = set()
        self.next_task_id = 0

app_state = AppState()

# =====================================================
# PRIORITY LOGIC
# =====================================================
def compute_area_score(h, e, r, s, p):
    return 5*h + 4*e + 3*r + 2*s + 0.5*(p / 1000)

def map_score_to_priority(score):
    if score >= 20: return 1
    if score >= 10: return 2
    if score >= 5: return 3
    return 4

def max_cut_hours_for_priority(priority):
    return {1: 0, 2: 3, 3: 6, 4: 12}[priority]

def calculate_total_demand():
    return sum(a["load_kw"] for a in app_state.areas.values())

# =====================================================
# DASHBOARD
# =====================================================
@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    priority_counts = [0, 0, 0, 0]
    for a in app_state.areas.values():
        priority_counts[a["priority"] - 1] += 1

    return jsonify({
        "total_demand": calculate_total_demand(),
        "available_power": app_state.P_avail_hour,
        "current_hour": f"{datetime.now().hour:02d}",
        "substations": app_state.substations,
        "priority_areas": priority_counts
    })

# =====================================================
# FEEDERS
# =====================================================
@app.route("/api/feeders", methods=["GET", "POST", "OPTIONS"])
def feeders_handler():
    if request.method == "OPTIONS":
        return "", 200

    if request.method == "GET":
        load_pf = defaultdict(float)
        areas_pf = defaultdict(int)
        for a in app_state.areas.values():
            load_pf[a["feeder_id"]] += a["load_kw"]
            areas_pf[a["feeder_id"]] += 1
        return jsonify({
            "feeders": app_state.feeders,
            "load_per_feeder": dict(load_pf),
            "areas_per_feeder": dict(areas_pf)
        })

    data = request.json
    fid = app_state.next_feeder_id
    app_state.next_feeder_id += 1

    app_state.feeders[fid] = {
        "id": fid,
        "name": data["name"],
        "capacity_kw": data.get("capacity_kw", 1000)
    }
    return jsonify({"success": True, "feeder_id": fid})

@app.route("/api/feeders/<int:fid>", methods=["DELETE"])
def delete_feeder(fid):
    if fid not in app_state.feeders:
        return jsonify({"error": "Feeder not found"}), 404

    del app_state.feeders[fid]
    app_state.areas = {k:v for k,v in app_state.areas.items() if v["feeder_id"] != fid}
    app_state.area_tree.rebuild(app_state.areas)
    return jsonify({"success": True})

# =====================================================
# AREAS
# =====================================================
@app.route("/api/areas", methods=["GET", "POST", "OPTIONS"])
def areas_handler():
    if request.method == "OPTIONS":
        return "", 200

    if request.method == "GET":
        feeder_names = {k:v["name"] for k,v in app_state.feeders.items()}
        return jsonify({"areas": app_state.areas, "feeder_names": feeder_names})

    data = request.json
    score = compute_area_score(
        data.get("hospitals",0),
        data.get("emergency_services",0),
        data.get("research_centers",0),
        data.get("schools",0),
        data.get("population",0)
    )

    aid = app_state.next_area_id
    app_state.next_area_id += 1

    app_state.areas[aid] = {
        "id": aid,
        "feeder_id": int(data["feeder_id"]),
        "name": data["name"],
        "load_kw": data.get("load_kw",0),
        "population": data.get("population",0),
        "priority": map_score_to_priority(score),
        "priority_score": score
    }

    app_state.area_cut_hours[aid] = 0
    app_state.area_last_cut_slot[aid] = -10
    app_state.area_tree.rebuild(app_state.areas)

    return jsonify({"success": True, "area_id": aid})

@app.route("/api/areas/<int:aid>", methods=["DELETE"])
def delete_area(aid):
    if aid not in app_state.areas:
        return jsonify({"error": "Area not found"}), 404
    del app_state.areas[aid]
    app_state.area_tree.rebuild(app_state.areas)
    return jsonify({"success": True})

# =====================================================
# LOAD SHEDDING (BST)
# =====================================================
def select_areas_for_cutting(power_needed, hour):
    if power_needed <= 0:
        return []

    selected, power_cut = [], 0
    for aid in app_state.area_tree.inorder():
        if power_cut >= power_needed:
            break

        a = app_state.areas[aid]
        cooldown = {4:2, 3:3, 2:4, 1:0}[a["priority"]]

        if (
            app_state.area_cut_hours[aid] < max_cut_hours_for_priority(a["priority"]) and
            hour - app_state.area_last_cut_slot[aid] > cooldown
        ):
            selected.append(aid)
            power_cut += a["load_kw"]
            app_state.area_cut_hours[aid] += 1
            app_state.area_last_cut_slot[aid] = hour

    return selected

# =====================================================
# SCHEDULE (6 AM → NEXT DAY 6 AM)
# =====================================================
@app.route("/api/schedule/generate", methods=["POST"])
def generate_schedule():
    energy = request.json.get("available_power",0)
    app_state.P_avail_hour = energy / 24 if energy > 0 else 0

    app_state.area_cut_hours = {aid:0 for aid in app_state.areas}
    app_state.area_last_cut_slot = {aid:-10 for aid in app_state.areas}

    base = datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)
    total_demand = calculate_total_demand()

    schedule = []
    for h in range(24):
        start = base + timedelta(hours=h)
        end = start + timedelta(hours=1)

        cut_needed = max(0, total_demand - app_state.P_avail_hour)
        areas_cut = select_areas_for_cutting(cut_needed, h)

        schedule.append({
            "hour": h,
            "start_time": start.strftime("%H:%M"),
            "end_time": end.strftime("%H:%M"),
            "is_cut": bool(areas_cut),
            "areas": areas_cut
        })

    app_state.schedule = schedule
    return jsonify({"schedule": schedule})

@app.route("/api/schedule", methods=["GET"])
def get_schedule():
    return jsonify({"schedule": app_state.schedule})

# =====================================================
# MAINTENANCE (STABLE PRIORITY QUEUE IMPLEMENTATION)
# =====================================================
@app.route("/api/maintenance", methods=["GET", "POST", "OPTIONS"])
def maintenance():
    if request.method == "OPTIONS":
        return "", 200

    # ---------- GET ----------
    if request.method == "GET":
        heap = []
        tasks = []

        # Build heap from ACTIVE tasks only
        for task_id, task in app_state.maintenance_map.items():
            heapq.heappush(
                heap,
                (task["area_priority"], task["timestamp"], task_id)
            )

        while heap:
            _, _, tid = heapq.heappop(heap)
            tasks.append(app_state.maintenance_map[tid])

        return jsonify({"queue": tasks})

    # ---------- POST ----------
    data = request.json

    try:
        area_id = int(data.get("area_id"))
    except:
        return jsonify({"error": "Invalid area ID"}), 400

    if area_id not in app_state.areas:
        return jsonify({"error": "Invalid area ID"}), 400

    issue = data.get("issue", "").strip()
    if not issue:
        return jsonify({"error": "Issue required"}), 400

    area = app_state.areas[area_id]

    task = {
        "id": app_state.next_task_id,
        "area_id": area_id,
        "area_name": area["name"],
        "area_priority": area["priority"],
        "issue": issue,
        "timestamp": datetime.now().isoformat()
    }

    app_state.maintenance_map[task["id"]] = task
    app_state.next_task_id += 1

    return jsonify({"success": True, "task": task}), 201


@app.route("/api/maintenance/<int:task_id>/resolve", methods=["POST", "OPTIONS"])
def resolve_maintenance(task_id):
    if request.method == "OPTIONS":
        return "", 200

    if task_id not in app_state.maintenance_map:
        return jsonify({"error": "Task not found"}), 404

    # 🔥 REMOVE COMPLETELY
    del app_state.maintenance_map[task_id]

    return jsonify({"success": True})


# =====================================================
# NETWORK GRAPH & HEALTH
# =====================================================
@app.route("/api/network/graph", methods=["GET"])
def network_graph():
    return jsonify({"feeders": app_state.feeders, "areas": app_state.areas})

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

# =====================================================
# MAIN
# =====================================================
if __name__ == "__main__":
    print("Backend running on http://localhost:5000")
    app.run(debug=True, host="0.0.0.0", port=5000)

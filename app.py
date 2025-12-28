import streamlit as st
import pandas as pd
from collections import defaultdict, deque

import networkx as nx
import matplotlib.pyplot as plt

# -------------------------------------------------
# Session state initialization
# -------------------------------------------------
if "substations" not in st.session_state:
    st.session_state.substations = {1: {"id": 1, "name": "Main Substation"}}

if "feeders" not in st.session_state:
    st.session_state.feeders = {}

if "areas" not in st.session_state:
    st.session_state.areas = {}

if "next_feeder_id" not in st.session_state:
    st.session_state.next_feeder_id = 1

if "next_area_id" not in st.session_state:
    st.session_state.next_area_id = 1

if "schedule" not in st.session_state:
    st.session_state.schedule = []  # 24-hour schedule

if "maintenance_queue" not in st.session_state:
    st.session_state.maintenance_queue = deque()

# scaling info for day energy method
if "day_factor_f" not in st.session_state:
    st.session_state.day_factor_f = 1.0
if "P_avail_hour" not in st.session_state:
    st.session_state.P_avail_hour = None

# area_id -> total hours of cuts assigned in the day
if "area_cut_hours" not in st.session_state:
    st.session_state.area_cut_hours = {}

# area_id -> last slot index when it was cut (for cool-down)
if "area_last_cut_slot" not in st.session_state:
    st.session_state.area_last_cut_slot = {}


# -------------------------------------------------
# Priority scoring logic (automatic, AREA level)
# -------------------------------------------------
def compute_area_score(num_hospitals, num_emergency, num_research, num_schools, population):
    """
    Weighted criticality model for area importance.[web:104][web:143]
    """
    score = (
        5 * num_hospitals
        + 4 * num_emergency
        + 3 * num_research
        + 2 * num_schools
        + 0.5 * (population / 1000.0)
    )
    return score


def map_score_to_priority(score):
    """
    Priority 1 = most important, Priority 4 = least important.
    """
    if score >= 20:
        return 1
    elif score >= 10:
        return 2
    elif score >= 5:
        return 3
    else:
        return 4


# -------------------------------------------------
# Demand helpers
# -------------------------------------------------
def calculate_total_demand():
    """
    Total hourly demand if all areas are ON (kW for 1-hour slot).[web:249]
    """
    return sum(a["load_kw"] for a in st.session_state.areas.values())


# -------------------------------------------------
# Helper: max cut hours per priority
# -------------------------------------------------
def max_cut_hours_for_priority(priority_level):
    """
    Max total cut hours in a day:
      P4 -> 12 h, P3 -> 6 h, P2 -> 3 h, P1 -> 0 h.[web:256]
    """
    if priority_level == 4:
        return 12
    elif priority_level == 3:
        return 6
    elif priority_level == 2:
        return 3
    else:
        return 0


# -------------------------------------------------
# Helper: was area cut recently? (no continuous cuts)
# -------------------------------------------------
def was_cut_in_recent_slots(area_id, current_slot_index, gap_slots=2):
    """
    Ensure at least `gap_slots` hours between cuts for the same area.[web:256]
    """
    last_slot = st.session_state.area_last_cut_slot.get(area_id, None)
    if last_slot is None:
        return False
    return (current_slot_index - last_slot) < gap_slots


# -------------------------------------------------
# 24-hour area-wise scheduling (using single daily energy input)
# -------------------------------------------------
def generate_area_schedule_for_slot(available_power, slot_start_hour, slot_duration, daily_schedule, slot_index):
    """
    Schedule for ONE hour slot with fairness constraints:
    - Priority order 4 -> 1.
    - Priority-based max cut hours (P4=12, P3=6, P2=3, P1=0).
    - At least 2-hour gap between cuts for the same area.[web:256][web:303]
    """
    total_demand = calculate_total_demand()
    if total_demand <= available_power:
        return

    shortage = total_demand - available_power
    if not st.session_state.areas:
        return

    if "area_cut_hours" not in st.session_state:
        st.session_state.area_cut_hours = {}
    if "area_last_cut_slot" not in st.session_state:
        st.session_state.area_last_cut_slot = {}

    areas_list = list(st.session_state.areas.values())

    # Sort from least important to most important (priority 4 -> 1)
    areas_sorted = sorted(
        areas_list,
        key=lambda a: (a["priority_level"], a["priority_score"]),
        reverse=True,
    )

    selected = []
    shed_sum = 0

    for area in areas_sorted:
        if area["priority_level"] == 1:
            # protect critical areas
            continue

        aid = area["id"]

        # avoid continuous cuts: require 2-slot gap
        if was_cut_in_recent_slots(aid, slot_index, gap_slots=2):
            continue

        already_cut = st.session_state.area_cut_hours.get(aid, 0)
        max_hours = max_cut_hours_for_priority(area["priority_level"])

        # Enforce priority-based max cut hours
        if already_cut >= max_hours:
            continue

        selected.append(area)
        shed_sum += area["load_kw"]

        # update total cut hours and last cut slot
        st.session_state.area_cut_hours[aid] = already_cut + slot_duration
        st.session_state.area_last_cut_slot[aid] = slot_index

        if shed_sum >= shortage:
            break

    slot_end_hour = (slot_start_hour + slot_duration) % 24
    for a in selected:
        start = f"{slot_start_hour:02d}:00"
        end = f"{slot_end_hour:02d}:00"
        daily_schedule.append(
            {
                "slot": slot_index,
                "start_time": start,
                "end_time": end,
                "area_id": a["id"],
                "area_name": a["name"],
                "feeder_id": a["feeder_id"],
                "feeder_name": st.session_state.feeders[a["feeder_id"]]["name"],
                "area_priority": a["priority_level"],
                # internal fields
                "area_score": a["priority_score"],
                "load_shed_kw": a["load_kw"],
                "energy_shed_kwh": a["load_kw"] * slot_duration,
            }
        )


def generate_daily_schedule_from_day_energy(E_day_kwh, base_hour=6, slot_duration=1):
    """
    Day-energy based 24-hour schedule with fairness constraints.[web:248][web:256]
    """
    st.session_state.area_cut_hours = {}
    st.session_state.area_last_cut_slot = {}
    daily_schedule = []

    D_hour = calculate_total_demand()
    E_needed = 24 * D_hour

    if D_hour == 0:
        st.session_state.day_factor_f = 1.0
        st.session_state.P_avail_hour = None
        return daily_schedule, "No demand (no areas)."

    if E_day_kwh >= E_needed:
        st.session_state.day_factor_f = 1.0
        st.session_state.P_avail_hour = D_hour
        return daily_schedule, (
            f"No shortage. Daily available energy {E_day_kwh:.1f} kWh "
            f">= required {E_needed:.1f} kWh."
        )

    f = E_day_kwh / E_needed
    P_avail = f * D_hour

    st.session_state.day_factor_f = f
    st.session_state.P_avail_hour = P_avail

    for slot_idx in range(24):
        slot_start_hour = (base_hour + slot_idx) % 24
        generate_area_schedule_for_slot(
            available_power=P_avail,
            slot_start_hour=slot_start_hour,
            slot_duration=slot_duration,
            daily_schedule=daily_schedule,
            slot_index=slot_idx,
        )

    st.session_state.schedule = daily_schedule
    msg = (
        f"Shortage exists. Daily required energy = {E_needed:.1f} kWh, "
        f"available = {E_day_kwh:.1f} kWh. "
        f"Uniform hourly available power used = {P_avail:.1f} kW "
        f"(scaling factor f = {f:.3f})."
    )
    return daily_schedule, msg


# -------------------------------------------------
# Daily energy division for graph
# -------------------------------------------------
def compute_feeder_daily_energy():
    """
    Compute daily supplied energy (kWh/day) per feeder, substation, plant,
    after applying the schedule.[web:248][web:253]
    """
    feeder_energy = defaultdict(float)   # kWh/day
    for aid, a in st.session_state.areas.items():
        cut_h = st.session_state.area_cut_hours.get(aid, 0)
        on_hours = 24 - cut_h
        if on_hours < 0:
            on_hours = 0
        e_area = on_hours * a["load_kw"]  # kWh/day
        feeder_energy[a["feeder_id"]] += e_area

    substation_energy = defaultdict(float)
    for fid, e in feeder_energy.items():
        sub_id = st.session_state.feeders[fid]["substation_id"]
        substation_energy[sub_id] += e

    plant_energy = sum(substation_energy.values())
    return feeder_energy, substation_energy, plant_energy


# -------------------------------------------------
# Build NetworkX graph with daily energy + OFF times
# -------------------------------------------------
def build_network_graph():
    """
    Graph:
      Plant -> Substations -> Feeders -> Areas

    Edge labels show daily supplied energy (kWh/day) after applying
    the 24-hour plan (power division for that particular day).[web:248][web:253]
    """
    G = nx.DiGraph()

    feeder_energy, substation_energy, plant_energy = compute_feeder_daily_energy()

    # Plant node
    G.add_node("Plant", layer=0, label="Power Plant")

    # Substations
    for sid, sub in st.session_state.substations.items():
        sub_node = f"S{sub['id']}"
        G.add_node(sub_node, layer=1, label=sub["name"])
        e_sub = substation_energy.get(sid, 0.0)
        G.add_edge("Plant", sub_node, energy_kwh=e_sub)

    # Feeders
    for fid, fdr in st.session_state.feeders.items():
        sub_node = f"S{fdr['substation_id']}"
        feeder_node = f"F{fid}"
        G.add_node(feeder_node, layer=2, label=fdr["name"])
        e_feed = feeder_energy.get(fid, 0.0)
        G.add_edge(sub_node, feeder_node, energy_kwh=e_feed)

    # Areas
    for aid, a in st.session_state.areas.items():
        feeder_node = f"F{a['feeder_id']}"
        area_node = f"A{aid}"
        label = f"{a['name']} (P{a['priority_level']})"
        G.add_node(area_node, layer=3, label=label)

        cut_h = st.session_state.area_cut_hours.get(aid, 0)
        on_hours = max(0, 24 - cut_h)
        e_area = on_hours * a["load_kw"]
        G.add_edge(feeder_node, area_node, energy_kwh=e_area)

    return G


def get_area_off_info_all():
    """
    For each area, collect list of all OFF intervals from schedule.
    Returns multiline text so each interval appears on a new line.[web:253][web:270]
    """
    slots_per_area = defaultdict(list)
    for rec in st.session_state.schedule:
        aid = rec["area_id"]
        slots_per_area[aid].append(f"{rec['start_time']}-{rec['end_time']}")

    info_text = {}
    for aid in st.session_state.areas.keys():
        if aid in slots_per_area and slots_per_area[aid]:
            joined = "\n".join(slots_per_area[aid])  # one per line
            info_text[aid] = f"OFF\n{joined}"
        else:
            info_text[aid] = "ON\n(full)"
    return info_text


def draw_network_graph():
    G = build_network_graph()
    if G.number_of_nodes() == 0:
        st.info("No nodes in the network yet.")
        return

    pos = {}
    layers = {}
    for node, data in G.nodes(data=True):
        layer = data.get("layer", 0)
        layers.setdefault(layer, []).append(node)

    for layer, nodes_in_layer in layers.items():
        x_spacing = 1.0
        start_x = - (len(nodes_in_layer) - 1) * x_spacing / 2
        for i, node in enumerate(nodes_in_layer):
            pos[node] = (start_x + i * x_spacing, -layer)

    labels = {n: d.get("label", n) for n, d in G.nodes(data=True)}

    plt.figure(figsize=(10, 6))
    nx.draw(
        G,
        pos,
        with_labels=True,
        labels=labels,
        node_color="#ffffff",
        edge_color="#555555",
        node_size=1800,
        font_size=8,
        font_weight="bold",
        linewidths=1,
        edgecolors="#000000",
        arrows=True,
        arrowstyle="-|>",
        arrowsize=12,
    )

    # Edge labels: daily energy division
    edge_labels = {}
    for u, v, data in G.edges(data=True):
        e = data.get("energy_kwh", 0.0)
        if e > 0:
            edge_labels[(u, v)] = f"{e:.1f} kWh/day"

    nx.draw_networkx_edge_labels(
        G,
        pos,
        edge_labels=edge_labels,
        font_size=7,
        font_color="blue",
    )

    # OFF / ON text below area circles (all intervals, stacked)
    off_info = get_area_off_info_all()
    for aid, a in st.session_state.areas.items():
        node = f"A{aid}"
        if node in pos:
            x, y = pos[node]
            text = off_info.get(aid, "ON\n(full)")
            plt.text(
                x,
                y - 0.25,
                text,
                fontsize=7,
                ha="center",
                va="top",
                color="black",
                fontweight="bold",
            )

    plt.axis("off")
    st.pyplot(plt.gcf())
    plt.close()


# -------------------------------------------------
# Streamlit UI
# -------------------------------------------------
st.set_page_config(
    page_title="Area-wise Day-Energy Power Cut Planner", layout="wide"
)

st.title("🔌 Area-wise Day-Energy Power Cut Planner")
st.caption(
    "Daily available energy (kWh) → uniform hourly power → 24-hour area-wise schedule "
    "with priority-based max cut hours and 2-hour cool-down, plus graph showing daily "
    "energy division and all OFF time slots under each area.[web:248][web:253][web:256]"
)

with st.sidebar:
    st.header("Network Hierarchy")

    st.markdown("**Power Plant** → Main Grid")
    substation_options = list(st.session_state.substations.keys())
    substation_id = st.selectbox(
        "Substation",
        options=substation_options,
        format_func=lambda sid: f"{sid} - {st.session_state.substations[sid]['name']}",
    )

    st.markdown("---")
    st.subheader("Feeders")

    feeder_name_new = st.text_input("New Feeder Name")
    if st.button("Add Feeder"):
        if feeder_name_new.strip():
            fid = st.session_state.next_feeder_id
            st.session_state.next_feeder_id += 1
            st.session_state.feeders[fid] = {
                "id": fid,
                "name": feeder_name_new.strip(),
                "substation_id": substation_id,
            }
            st.success(f"Feeder '{feeder_name_new}' added.")
        else:
            st.error("Enter a feeder name.")

    if st.session_state.feeders:
        feeder_ids_for_sub = [
            fid
            for fid, f in st.session_state.feeders.items()
            if f["substation_id"] == substation_id
        ]
        if feeder_ids_for_sub:
            selected_feeder_id = st.selectbox(
                "Select Feeder",
                options=feeder_ids_for_sub,
                format_func=lambda fid: f"{fid} - {st.session_state.feeders[fid]['name']}",
            )
        else:
            st.info("No feeders for this substation yet.")
            selected_feeder_id = None
    else:
        st.info("No feeders defined yet.")
        selected_feeder_id = None

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    [
        "Areas & Auto Priority",
        "Area Importance",
        "Day-Energy Scheduling",
        "Network Graph",
        "Maintenance Queue",
    ]
)


# -------------------------------------------------
# Tab 1: Areas & Auto Priority
# -------------------------------------------------
with tab1:
    st.subheader("Areas under Selected Feeder")

    if selected_feeder_id is None:
        st.info("Select or create a feeder in the sidebar to add areas.")
    else:
        with st.form("add_area_form"):
            col1, col2 = st.columns(2)

            with col1:
                name = st.text_input("Area Name")
                load_kw = st.number_input(
                    "Average energy used in 1 hour (kW / kWh)",
                    min_value=0.0,
                    value=100.0,
                    step=10.0,
                )
                population = st.number_input(
                    "Population (approx.)", min_value=0, value=1000, step=100
                )

            with col2:
                num_hospitals = st.number_input(
                    "Number of Hospitals", min_value=0, value=0, step=1
                )
                num_emergency = st.number_input(
                    "Emergency Centers (police/fire/ambulance)",
                    min_value=0,
                    value=0,
                    step=1,
                )
                num_research = st.number_input(
                    "Research Institutions", min_value=0, value=0, step=1
                )
                num_schools = st.number_input(
                    "Schools/Colleges", min_value=0, value=0, step=1
                )

            submitted = st.form_submit_button("Add Area")

            if submitted:
                if not name.strip():
                    st.error("Enter area name.")
                else:
                    score = compute_area_score(
                        num_hospitals=num_hospitals,
                        num_emergency=num_emergency,
                        num_research=num_research,
                        num_schools=num_schools,
                        population=population,
                    )
                    priority_level = map_score_to_priority(score)

                    aid = st.session_state.next_area_id
                    st.session_state.next_area_id += 1

                    st.session_state.areas[aid] = {
                        "id": aid,
                        "name": name.strip(),
                        "feeder_id": selected_feeder_id,
                        "load_kw": load_kw,
                        "population": population,
                        "num_hospitals": num_hospitals,
                        "num_emergency": num_emergency,
                        "num_research": num_research,
                        "num_schools": num_schools,
                        "priority_score": score,
                        "priority_level": priority_level,
                    }

                    st.success(
                        f"Area '{name}' added. Auto priority = {priority_level} (score {score:.1f})."
                    )

        st.markdown("---")
        st.subheader("Areas List with Auto Priority (This Feeder)")

        feeder_areas = [
            a
            for a in st.session_state.areas.values()
            if a["feeder_id"] == selected_feeder_id
        ]

        if feeder_areas:
            df = pd.DataFrame(feeder_areas)
            df_display = df[
                [
                    "id",
                    "name",
                    "load_kw",
                    "population",
                    "num_hospitals",
                    "num_emergency",
                    "num_research",
                    "num_schools",
                    "priority_score",
                    "priority_level",
                ]
            ]
            st.dataframe(df_display, use_container_width=True)
        else:
            st.info("No areas added for this feeder yet.")


# -------------------------------------------------
# Tab 2: Area Importance (all areas)
# -------------------------------------------------
with tab2:
    st.subheader("All Areas: Priority and Details")

    if st.session_state.areas:
        df_all = pd.DataFrame(st.session_state.areas.values())
        df_all_display = df_all[
            [
                "id",
                "name",
                "feeder_id",
                "load_kw",
                "population",
                "num_hospitals",
                "num_emergency",
                "num_research",
                "num_schools",
                "priority_score",
                "priority_level",
            ]
        ]
        st.dataframe(df_all_display, use_container_width=True)
        st.caption(
            "Area priority is computed automatically using a weighted criticality score.[web:104][web:143]"
        )
    else:
        st.info("No areas defined yet.")


# -------------------------------------------------
# Tab 3: Day-Energy Scheduling
# -------------------------------------------------
with tab3:
    st.subheader("Day-Energy Based 24-hour Scheduling (06:00 to next-day 06:00)")

    D_hour = calculate_total_demand()
    st.info(
        f"If all areas are ON, hourly demand = {D_hour:.1f} kW, "
        f"daily energy needed = {24*D_hour:.1f} kWh."
    )

    E_day = st.number_input(
        "Available energy for the whole day (kWh)",
        min_value=0.0,
        value=max(0.0, 24 * D_hour - 200),
        step=10.0,
    )

    if st.button("Generate 24-hour Schedule from Daily Energy"):
        daily, msg = generate_daily_schedule_from_day_energy(
            E_day_kwh=E_day, base_hour=6, slot_duration=1
        )
        if daily:
            st.success(msg)
        else:
            st.warning(msg)

    st.markdown("---")
    st.subheader("Daily Power Cut Schedule (Area-wise)")

    if st.session_state.schedule:
        df_sched = pd.DataFrame(st.session_state.schedule)
        df_sched = df_sched.sort_values(
            ["slot", "area_priority"], ascending=[True, False]
        )
        cols_to_show = [
            "slot",
            "start_time",
            "end_time",
            "area_id",
            "area_name",
            "feeder_id",
            "feeder_name",
            "area_priority",
        ]
        df_sched = df_sched[cols_to_show]
        st.dataframe(df_sched, use_container_width=True)
    else:
        st.info("No daily schedule generated yet.")


# -------------------------------------------------
# Tab 4: Network Graph Visualization
# -------------------------------------------------
with tab4:
    st.subheader("Electric Distribution Network (Graph View)")
    st.markdown(
        "Edges show daily supplied energy (kWh/day) based on the 24-hour schedule. "
        "All OFF time slots for each area are displayed one per line below the circle.[web:248][web:253][web:270]"
    )
    if st.button("Refresh Network Graph"):
        draw_network_graph()
    else:
        draw_network_graph()


# -------------------------------------------------
# Tab 5: Maintenance Queue (FIFO)
# -------------------------------------------------
with tab5:
    st.subheader("Maintenance Task Queue (FIFO)")

    if st.session_state.areas:
        with st.form("maintenance_form"):
            area_choices = list(st.session_state.areas.keys())
            area_id_for_task = st.selectbox(
                "Select Area",
                options=area_choices,
                format_func=lambda aid: f"{aid} - {st.session_state.areas[aid]['name']}",
            )
            description = st.text_input("Maintenance issue / task description")
            submit_task = st.form_submit_button("Add Task")

            if submit_task:
                if not description.strip():
                    st.error("Enter a task description.")
                else:
                    st.session_state.maintenance_queue.append(
                        {"area_id": area_id_for_task, "description": description.strip()}
                    )
                    st.success("Task added to maintenance queue (FIFO).")
    else:
        st.info("Add some areas first to attach maintenance tasks.")

    st.markdown("---")
    st.subheader("Pending Maintenance Tasks")

    if st.session_state.maintenance_queue:
        tasks = []
        for t in list(st.session_state.maintenance_queue):
            a = st.session_state.areas.get(t["area_id"])
            tasks.append(
                {
                    "Area ID": t["area_id"],
                    "Area Name": a["name"] if a else "Unknown",
                    "Description": t["description"],
                }
            )
        st.table(pd.DataFrame(tasks))
    else:
        st.info("No pending maintenance tasks.")

    if st.button("Process Next Task (Dequeue)"):
        if st.session_state.maintenance_queue:
            t = st.session_state.maintenance_queue.popleft()
            a = st.session_state.areas.get(t["area_id"])
            st.success(
                f"Processed task for Area {t['area_id']} - {a['name'] if a else 'Unknown'}: {t['description']}"
            )
        else:
            st.warning("Queue is empty.")

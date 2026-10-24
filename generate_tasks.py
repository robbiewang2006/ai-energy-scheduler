import csv, random

random.seed(20251024)

HORIZON = 8760
SPARE_IT_KW = 25.0
URGENCY = {"high": (1, 12), "medium": (12, 72), "low": (72, 336)}
NO_DEADLINE_FRACTION = 0.40
NOMINAL_WINDOW = 168  # nominal week-long window used to size no-deadline energy


def make_task(tid):
    urgency = random.choices(
        ["low", "medium", "high"], weights=[0.5, 0.35, 0.15]
    )[0]
    if random.random() < NO_DEADLINE_FRACTION:
        start_time = random.randint(0, HORIZON - 1)
        deadline = ""
        energy = round(random.uniform(0.15, 0.85) * NOMINAL_WINDOW * SPARE_IT_KW, 2)
        duration = NOMINAL_WINDOW  # open-ended: use the nominal window as its duration
    else:
        lo, hi = URGENCY[urgency]
        window = random.randint(lo, hi)
        start_time = random.randint(0, HORIZON - window)
        deadline = start_time + window
        energy = round(random.uniform(0.15, 0.85) * window * SPARE_IT_KW, 2)
        duration = window
    return {
        "task_id": f"T{tid:05d}",
        "urgency_level": urgency,
        "start_time": start_time,
        "deadline": deadline,
        "duration": duration,
        "energy_needed": energy,
    }


N = 438
tasks = sorted((make_task(i + 1) for i in range(N)), key=lambda t: t["start_time"])

with open("data/tasks.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(
        f,
        fieldnames=["task_id", "urgency_level", "start_time", "deadline", "duration", "energy_needed"],
    )
    w.writeheader()
    w.writerows(tasks)

no_dl = sum(1 for t in tasks if t["deadline"] == "")
print(
    f"Wrote {len(tasks)} tasks ({no_dl} no-deadline); start_time "
    f"{tasks[0]['start_time']}..{tasks[-1]['start_time']}; "
    f"total {sum(t['energy_needed'] for t in tasks):.0f} kWh over {HORIZON} hours"
)

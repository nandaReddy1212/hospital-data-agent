"""Builds hospital.db: a synthetic hospital database for the agent to query.

All data is randomly generated. No real patient information is used.
Run from the project root:  python setup_db.py
Re-running deletes and rebuilds the database with the same data (fixed seed).
"""

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "hospital.db"
SEED = 42
NUM_PATIENTS = 300
NUM_VISITS = 1500

FIRST_NAMES = ["Asha", "Ben", "Carla", "Dev", "Elena", "Farid", "Grace", "Hiro", "Isla",
               "Jamal", "Kavya", "Liam", "Maya", "Noah", "Olivia", "Priya", "Quinn",
               "Rosa", "Sam", "Tara", "Uma", "Victor", "Wei", "Yara", "Zane"]
LAST_NAMES = ["Patel", "Garcia", "Kim", "Nguyen", "Smith", "Okafor", "Rossi", "Silva",
              "Chen", "Khan", "Lopez", "Brown", "Reddy", "Muller", "Haddad", "Ito"]
CITIES = ["Charlotte", "Matthews", "Concord", "Gastonia", "Huntersville", "Monroe"]
INSURANCE = ["Medicare", "Medicaid", "Commercial", "Self-pay"]

DEPARTMENTS = [
    # (name, floor, base_charge)
    ("Cardiology", 3, 850),
    ("Radiology", 1, 600),
    ("Emergency", 1, 1200),
    ("Orthopedics", 2, 700),
    ("Pediatrics", 4, 400),
    ("Oncology", 5, 1500),
    ("Neurology", 3, 950),
    ("General Medicine", 2, 300),
]

VISIT_TYPES = ["Outpatient", "Inpatient", "Emergency", "Follow-up"]
DIAGNOSES = {
    "Cardiology": ["Hypertension", "Arrhythmia", "Chest pain"],
    "Radiology": ["Imaging review", "Fracture screening", "Mass evaluation"],
    "Emergency": ["Laceration", "Sprain", "Abdominal pain", "Fever"],
    "Orthopedics": ["Knee pain", "Back pain", "Fracture follow-up"],
    "Pediatrics": ["Well-child visit", "Ear infection", "Asthma"],
    "Oncology": ["Chemotherapy session", "Oncology consult"],
    "Neurology": ["Migraine", "Seizure follow-up", "Neuropathy"],
    "General Medicine": ["Annual physical", "Flu-like symptoms", "Diabetes check"],
}

SCHEMA = """
CREATE TABLE departments (
    department_id   INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    floor           INTEGER NOT NULL
);

CREATE TABLE doctors (
    doctor_id       INTEGER PRIMARY KEY,
    first_name      TEXT NOT NULL,
    last_name       TEXT NOT NULL,
    department_id   INTEGER NOT NULL REFERENCES departments(department_id),
    hire_date       TEXT NOT NULL
);

CREATE TABLE patients (
    patient_id      INTEGER PRIMARY KEY,
    first_name      TEXT NOT NULL,
    last_name       TEXT NOT NULL,
    birth_date      TEXT NOT NULL,
    gender          TEXT NOT NULL,
    city            TEXT NOT NULL,
    insurance_type  TEXT NOT NULL
);

CREATE TABLE visits (
    visit_id        INTEGER PRIMARY KEY,
    patient_id      INTEGER NOT NULL REFERENCES patients(patient_id),
    doctor_id       INTEGER NOT NULL REFERENCES doctors(doctor_id),
    department_id   INTEGER NOT NULL REFERENCES departments(department_id),
    visit_date      TEXT NOT NULL,
    visit_type      TEXT NOT NULL,
    diagnosis       TEXT NOT NULL,
    total_charge    REAL NOT NULL
);
"""


def random_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, (end - start).days))


def build() -> None:
    rng = random.Random(SEED)
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    # Departments
    conn.executemany(
        "INSERT INTO departments VALUES (?, ?, ?)",
        [(i, name, floor) for i, (name, floor, _) in enumerate(DEPARTMENTS, start=1)],
    )

    # Doctors: 3 per department
    doctors = []
    doctor_id = 1
    for dept_id in range(1, len(DEPARTMENTS) + 1):
        for _ in range(3):
            doctors.append((
                doctor_id,
                rng.choice(FIRST_NAMES),
                rng.choice(LAST_NAMES),
                dept_id,
                random_date(rng, date(2010, 1, 1), date(2025, 12, 31)).isoformat(),
            ))
            doctor_id += 1
    conn.executemany("INSERT INTO doctors VALUES (?, ?, ?, ?, ?)", doctors)

    # Patients
    patients = [
        (
            pid,
            rng.choice(FIRST_NAMES),
            rng.choice(LAST_NAMES),
            random_date(rng, date(1940, 1, 1), date(2022, 12, 31)).isoformat(),
            rng.choice(["F", "M"]),
            rng.choice(CITIES),
            rng.choices(INSURANCE, weights=[30, 20, 40, 10])[0],
        )
        for pid in range(1, NUM_PATIENTS + 1)
    ]
    conn.executemany("INSERT INTO patients VALUES (?, ?, ?, ?, ?, ?, ?)", patients)

    # Visits: some departments are busier than others
    dept_weights = [14, 16, 20, 12, 10, 6, 8, 14]
    doctors_by_dept = {}
    for d in doctors:
        doctors_by_dept.setdefault(d[3], []).append(d[0])

    visits = []
    for vid in range(1, NUM_VISITS + 1):
        dept_id = rng.choices(range(1, len(DEPARTMENTS) + 1), weights=dept_weights)[0]
        dept_name, _, base_charge = DEPARTMENTS[dept_id - 1]
        visit_type = "Emergency" if dept_name == "Emergency" else rng.choice(
            ["Outpatient", "Inpatient", "Follow-up"])
        multiplier = 3.0 if visit_type == "Inpatient" else 1.0
        charge = round(base_charge * multiplier * rng.uniform(0.7, 1.4), 2)
        visits.append((
            vid,
            rng.randint(1, NUM_PATIENTS),
            rng.choice(doctors_by_dept[dept_id]),
            dept_id,
            random_date(rng, date(2025, 1, 1), date(2026, 9, 30)).isoformat(),
            visit_type,
            rng.choice(DIAGNOSES[dept_name]),
            charge,
        ))
    conn.executemany("INSERT INTO visits VALUES (?, ?, ?, ?, ?, ?, ?, ?)", visits)

    conn.commit()

    for table in ["departments", "doctors", "patients", "visits"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table:<12} {count:>5} rows")
    conn.close()
    print(f"\nCreated {DB_PATH}")


if __name__ == "__main__":
    build()
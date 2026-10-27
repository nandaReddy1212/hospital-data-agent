import sqlite3

conn = sqlite3.connect("hospital.db")
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS patients (id INTEGER, name TEXT, age INTEGER)")
cur.execute("CREATE TABLE IF NOT EXISTS visits (id INTEGER, patient_id INTEGER, visit_date TEXT, department TEXT)")
cur.executemany("INSERT INTO patients VALUES (?, ?, ?)",
                [(1, "Asha", 34), (2, "Ben", 58), (3, "Carla", 41)])
cur.executemany("INSERT INTO visits VALUES (?, ?, ?, ?)",
                [(1, 1, "2026-09-01", "Cardiology"), (2, 2, "2026-09-03", "Radiology"),
                 (3, 1, "2026-09-10", "Cardiology"), (4, 3, "2026-09-12", "ER")])
conn.commit()
conn.close()
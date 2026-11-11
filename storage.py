"""Local SQLite persistence for COMP902 Streamlit profiles and results."""
import json
import sqlite3
import secrets
import string
import hashlib
import hmac
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / 'comp902_app.sqlite3'
# Set to 0 to disable automatic profile pruning. Does not limit research CSV imports.
MAX_SAVED_PROFILES = int(os.getenv('COMP902_MAX_PROFILES', '500'))
ARCHIVE_DIR = DB_PATH.parent / 'database_backups'



def connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute('PRAGMA busy_timeout = 15000')
    conn.execute('''CREATE TABLE IF NOT EXISTS profiles (
        student_id TEXT PRIMARY KEY,
        profile_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS timetable_results (
        result_id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        algorithm TEXT NOT NULL,
        profile_json TEXT NOT NULL,
        result_json TEXT NOT NULL,
        generated_at TEXT NOT NULL
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS profile_access (
        student_id TEXT PRIMARY KEY,
        pin_salt TEXT NOT NULL,
        pin_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    )''')
    conn.commit()
    return conn


def load_profiles():
    with connect() as conn:
        rows = conn.execute('SELECT student_id, profile_json FROM profiles ORDER BY student_id').fetchall()
    return {sid: json.loads(payload) for sid, payload in rows}


def save_profile(profile, previous_id=None):
    sid = str(profile.get('student_id', '')).strip()
    if not sid:
        raise ValueError('Student ID is required.')
    payload = json.dumps(profile, ensure_ascii=False)
    timestamp = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        if previous_id and previous_id != sid:
            # Avoid overwriting a different existing student during renaming.
            existing = conn.execute('SELECT 1 FROM profiles WHERE student_id=?', (sid,)).fetchone()
            if existing:
                raise ValueError(f'Student ID {sid} already exists in saved profiles.')
            conn.execute('DELETE FROM profiles WHERE student_id=?', (previous_id,))
        conn.execute('''INSERT INTO profiles(student_id,profile_json,updated_at) VALUES (?,?,?)
            ON CONFLICT(student_id) DO UPDATE SET profile_json=excluded.profile_json,
            updated_at=excluded.updated_at''', (sid, payload, timestamp))
        conn.commit()


def delete_profile(sid):
    with connect() as conn:
        conn.execute('DELETE FROM profiles WHERE student_id=?', (sid,))
        conn.commit()


def save_results(profile, baseline, priority):
    timestamp = datetime.now(timezone.utc).isoformat()
    profile_json = json.dumps(profile, ensure_ascii=False)
    with connect() as conn:
        for algorithm, result in [('baseline', baseline), ('priority', priority)]:
            payload = {
                'metrics': result['metrics'],
                'allocations': result['allocations'].to_dict(orient='records'),
                'timetable': result['timetable'].to_dict(orient='records'),
                'conflicts': result['conflicts'].to_dict(orient='records'),
                'warnings': result.get('warnings', []),
            }
            conn.execute('''INSERT INTO timetable_results
                (student_id,algorithm,profile_json,result_json,generated_at)
                VALUES (?,?,?,?,?)''',
                (profile['student_id'], algorithm, profile_json, json.dumps(payload, ensure_ascii=False), timestamp))
        conn.commit()


def _hash_pin(pin, salt):
    return hashlib.pbkdf2_hmac('sha256', pin.encode('utf-8'), bytes.fromhex(salt), 250000).hex()


def new_profile_credentials():
    """Generate an unpredictable ID and independent six-digit PIN."""
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    with connect() as conn:
        for _ in range(100):
            sid = 'STU-' + ''.join(secrets.choice(alphabet) for _ in range(10))
            if not conn.execute('SELECT 1 FROM profiles WHERE student_id=?', (sid,)).fetchone():
                pin = ''.join(secrets.choice(string.digits) for _ in range(6))
                return sid, pin
    raise RuntimeError('Could not generate a unique profile ID.')


def profile_count():
    with connect() as conn:
        return conn.execute('SELECT COUNT(*) FROM profiles').fetchone()[0]


def backup_database():
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    name = datetime.now(timezone.utc).strftime('comp902_backup_%Y%m%d_%H%M%S_%f.sqlite3')
    target = ARCHIVE_DIR / name
    with connect() as source, sqlite3.connect(target) as destination:
        source.backup(destination)
    return target


def load_profile_by_credentials(sid, pin):
    """Never expose whether ID or PIN was incorrect."""
    sid = str(sid).strip().upper()
    with connect() as conn:
        row = conn.execute("""SELECT p.profile_json,a.pin_salt,a.pin_hash
            FROM profiles p JOIN profile_access a ON p.student_id=a.student_id
            WHERE p.student_id=?""", (sid,)).fetchone()
    if row is None or not hmac.compare_digest(_hash_pin(str(pin), row[1]), row[2]):
        raise ValueError('Profile ID or PIN is incorrect, or this is a legacy profile.')
    return json.loads(row[0])


def save_secured_profile(profile, pin):
    """Create or update a PIN-protected profile; prune only after safe backup."""
    sid = str(profile.get('student_id', '')).strip().upper()
    if not sid.startswith('STU-') or not pin or len(str(pin)) != 6 or not str(pin).isdigit():
        raise ValueError('A generated STU- Profile ID and six-digit PIN are required.')
    payload = json.dumps(profile, ensure_ascii=False)
    timestamp = datetime.now(timezone.utc).isoformat()
    salt = secrets.token_hex(16)
    with connect() as conn:
        existing = conn.execute('SELECT pin_salt,pin_hash FROM profile_access WHERE student_id=?', (sid,)).fetchone()
        if existing and not hmac.compare_digest(_hash_pin(str(pin), existing[0]), existing[1]):
            raise ValueError('Incorrect PIN. Profile was not overwritten.')
        # Do not silently overwrite legacy records that happen to have the same ID.
        if not existing and conn.execute('SELECT 1 FROM profiles WHERE student_id=?', (sid,)).fetchone():
            raise ValueError('This ID already belongs to a legacy profile.')
        new_record = existing is None
        count = conn.execute('SELECT COUNT(*) FROM profiles').fetchone()[0]
    archive = None
    if new_record and MAX_SAVED_PROFILES > 0 and count >= MAX_SAVED_PROFILES:
        archive = backup_database()  # Backup before any removal, including results.
    with connect() as conn:
        if new_record:
            conn.execute('INSERT INTO profile_access VALUES (?,?,?,?)', (sid, salt, _hash_pin(str(pin),salt),timestamp))
        conn.execute("""INSERT INTO profiles(student_id,profile_json,updated_at) VALUES (?,?,?)
            ON CONFLICT(student_id) DO UPDATE SET profile_json=excluded.profile_json, updated_at=excluded.updated_at""",
            (sid,payload,timestamp))
        pruned = []
        if MAX_SAVED_PROFILES > 0:
            count = conn.execute('SELECT COUNT(*) FROM profiles').fetchone()[0]
            excess = max(0,count-MAX_SAVED_PROFILES)
            if excess:
                # Only remove secured profiles automatically; legacy research records are protected.
                old = conn.execute("""SELECT p.student_id FROM profiles p
                    JOIN profile_access a ON a.student_id=p.student_id
                    WHERE p.student_id<>? ORDER BY a.created_at ASC LIMIT ?""",(sid,excess)).fetchall()
                if len(old)<excess:
                    conn.rollback()
                    raise ValueError('Profile limit reached, but protected legacy records prevent safe pruning. Increase COMP902_MAX_PROFILES.')
                pruned=[r[0] for r in old]
                for old_sid in pruned:
                    conn.execute('DELETE FROM timetable_results WHERE student_id=?',(old_sid,))
                    conn.execute('DELETE FROM profile_access WHERE student_id=?',(old_sid,))
                    conn.execute('DELETE FROM profiles WHERE student_id=?',(old_sid,))
        conn.commit()
    return {'pruned':pruned,'archive':str(archive) if archive else None}


def load_legacy_profile(sid):
    """Local administrator migration only; never expose all records to parents online."""
    with connect() as conn:
        row=conn.execute("""SELECT p.profile_json FROM profiles p
            LEFT JOIN profile_access a ON a.student_id=p.student_id
            WHERE p.student_id=? AND a.student_id IS NULL""",(sid,)).fetchone()
    return json.loads(row[0]) if row else None

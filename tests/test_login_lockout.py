from datetime import datetime, timedelta

from login_lockout import BANNED, Gate, gate, locked_until

NOW = datetime(2026, 11, 10, 12, 0)


def test_attempts_left_go_on_to_the_password():
    assert gate(3, None, NOW, 15) == Gate(None)


def test_null_attempts_is_a_ban_without_end():
    assert gate(None, NOW - timedelta(days=1), NOW, 15) == Gate(BANNED)


def test_running_lock_refuses_until_its_end():
    since = NOW - timedelta(minutes=5)
    assert gate(0, since, NOW, 15) == Gate(locked_until(since + timedelta(minutes=15)))


def test_expired_lock_restores_the_attempts():
    assert gate(0, NOW - timedelta(minutes=15), NOW, 15) == Gate(None, restore=True)


def test_lock_without_unlock_date_starts_its_countdown_now():
    assert gate(0, None, NOW, 15) == Gate(locked_until(NOW + timedelta(minutes=15)), start_lock=True)


def test_locked_message_shows_the_time():
    assert locked_until(datetime(2026, 11, 10, 9, 5)) == "Учётная запись заблокирована до 09:05"

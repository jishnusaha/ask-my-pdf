---
name: live-testing-traps
description: Traps hit when running live curl checks against the ask-my-pdf dev server and Postgres (chat ids, .env access, ruff exit code)
metadata:
  type: project
---

- Dev DB contents drift: a packet named chat 7, but only chat 11 existed (0 messages). Query `Chat.objects.values_list('id','title')` via `uv run python manage.py shell -c ...` before choosing a chat id; if the packet's id is gone, use the real one and report the deviation.
- Bash reads of `.env` (even `grep` for the port) are denied by the permission system. Get the DB port/name from `django.conf.settings.DATABASES['default']` in `manage.py shell` instead (dev DB = port 9002, db `app`). Never print the OpenAI key.
- `ChatMessage` ids keep incrementing after deletes (empty table still had ids ~109), so "max id" can be `None` while new rows get large ids; clean up by the explicit id list you observed in the `done` event.
- `uv run ruff check .` exits 1 because of the one pre-existing F401 in `chats/tests.py` (`TestCase`); that is expected, check the finding list, not the exit code.
- Server start takes ~14 s (cross-encoder load); poll `GET /chats/api/` until 200 rather than sleeping a fixed time. Run with `--noreload` on a spare port (9011) and `pkill -f 'manage.py runserver 9011'` afterwards.

# Vulnerable CRUD Lab

Local-only intentionally vulnerable CRUD app for testing scanners and security
checks. It uses Python standard library HTTP handling plus SQLite, so no
dependencies are required.

## Run

```sh
python3 app.py
```

Open:

```text
http://127.0.0.1:8088
```

The app creates `vulnerable.db` on first run.

## Seed Users

```text
admin / admin123
alice / password1
bob / password2
```

## Intentional Vulnerabilities

- SQL injection in `/api/login` and `/api/search`
- IDOR on note read/update/delete endpoints
- Stored XSS in note bodies rendered by the UI
- Reflected XSS through the `?banner=` page query parameter
- Mass assignment on `owner_id` and `is_public`
- Plaintext passwords in SQLite
- Weak token model: the UI/API trusts `X-User-Id`
- Debug data leak at `/api/debug/users`
- Permissive CORS and no CSRF protection

Do not expose this app to a network you do not control.

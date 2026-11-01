# Local API access

The runtime's HTTP and WebSocket API (`h2m/server.py`) can move the arm, so it only
answers the operator UI it serves itself.

| Check | Rule | Failure |
|---|---|---|
| Host | `Host` must be `127.0.0.1` or `localhost` (any port). Blocks DNS rebinding. | 403 `host not allowed` |
| Origin | If a request (or the `/api/live` WebSocket) has an `Origin`, it must be this same origin (`http://<Host>`). Blocks other web pages in the browser. | 403 `origin not allowed`, WebSocket closed with 1008 |
| Token | `POST`, `PUT`, `PATCH` and `DELETE` need header `X-H2M-Token: <token>`, except `POST /api/stop` (stopping is always safe, so STOP works even from a page without the token). | 403 `missing or wrong session token` |

The token is random per process (`secrets.token_urlsafe(32)`) and changes on every
restart. `GET /` writes it into the page as `<meta name="h2m-token" content="...">`
(served with `Cache-Control: no-store`). `web/js/core.js` reads that tag once and sends
the header on every `api(...)` call, so views that use `api` need nothing else. A
different site cannot read the page (no CORS headers), so it cannot get the token.

Scripts and `curl` (no `Origin` header) read the token from the page first:

```bash
TOKEN=$(curl -s http://127.0.0.1:8080/ | sed -n 's/.*name="h2m-token" content="\([^"]*\)".*/\1/p')
curl -X POST -H "X-H2M-Token: $TOKEN" http://127.0.0.1:8080/api/stop
```

`GET` endpoints (state, cameras, rules, incidents) need no token but still pass the
Host and Origin checks. Tests build the app with `create_app(h2m, token="...")` and a
`TestClient(..., base_url="http://127.0.0.1")`.

## Operator gripper release (`POST /api/gripper/release`)

Opens the gripper alone, outside any plan: the exit when a stop left an object in the
jaws. It is flagged for safety review and **off by default**.

- `safety.operator_release: false` (shipped): every call answers 403 with
  `the operator gripper release is off (set safety.operator_release: true in
  configs/h2m.yaml to enable it)`. Only the literal `true` enables it.
- When enabled, the release is refused (409) unless the arm is READY (a STOPPED arm is
  reset and armed first), nothing is in flight, the EMG guard is clear, and the measured
  arm joints are within `operator_release_tol_deg` of a pose in
  `operator_release_poses` (default: `tray_release`, `tray_above`, every slot's grasp
  pose). Handover poses never count, even if listed. Elsewhere the answer is
  `bring it to the tray first: ...`. On hardware only taught poses count.

Two steps, both with the session token:

1. **Offer.** POST with no body, `{}`, or any JSON object without `release_id`
   (`{"confirm": true}` included). Nothing moves. 200:

   ```json
   {"ok": true, "release_id": "Xy...", "pose": "tray_release",
    "holding": {"status": "holding", "object_id": "cup", "name": "paper cup", "why": "..."},
    "expires_in_s": 10.0,
    "detail": "This opens my gripper at tray_release: the paper cup will drop. ..."}
   ```

2. **Commit.** POST `{"release_id": "<id>"}` within `operator_release_confirm_s`. 200
   `{"ok": true, "detail": "releasing the gripper"}`. The id is single use: any commit
   attempt (right or wrong id) consumes the open offer. It is refused (409, ask again)
   if it expired, if anything was cancelled or stopped since (cancel, stop, brace, open
   hand, disarm), if the arm was re-armed, if what the gripper holds changed, or if the
   arm moved off the offered pose (re-checked under the arm lock).

A body that is not a JSON object answers 400. While the gripper opens, brace, open
hand, cancel, stop and any arm guard problem (EMG stale or calibrating) stop the arm
where it is; the gripper contents are then unknown. The gripper reads EMPTY only once
the measured opening reaches `gripper_open`. `GET /api/state` shows
`operator_release: {enabled, offer: {pose, holding, expires_in_s}}` (never the id).

```bash
curl -s -X POST -H "X-H2M-Token: $TOKEN" http://127.0.0.1:8080/api/gripper/release
curl -s -X POST -H "X-H2M-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"release_id": "<id from the first call>"}' http://127.0.0.1:8080/api/gripper/release
```

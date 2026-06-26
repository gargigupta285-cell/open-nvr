# Watchlist / face enrollment

The agent can keep a watchlist of known people: enroll a face from a live
camera, list who's enrolled, and forget someone — all by voice or via the
`/people` endpoints.

**No model is built or trained here.** Enrollment forwards a captured frame to
the recognition adapter's faces API; recognition itself is the InsightFace
adapter. If `faces_url` is unset, the enroll/list/forget tools simply report
that face management isn't configured.

## Configure

```yaml
# config.yml
faces_url: http://insightface-adapter:9100   # recognition adapter base URL
faces_token: ${INTERNAL_API_KEY}
```

## Voice / tools
- `enroll_face(name, camera_id, category?)` — "remember the person at the door as Alex"
- `list_people()` — "who do you know?"
- `forget_face(name)` — "forget Alex"

## Endpoints
- `GET /people` → `{configured, people}`
- `POST /people` → `{name, camera_id, category?}` (captures a frame, enrolls)
- `DELETE /people/{name}`

## Expected faces API contract (adapter side)

The `FaceClient` assumes this shape — adjust if your adapter version differs:

- `POST {faces_url}/faces` with `{name, category, frame_b64}` → enroll
- `GET {faces_url}/faces` → `{people: [{name, category}, …]}` (also accepts a
  bare list or `{faces: […]}`)
- `DELETE {faces_url}/faces/{name}` → forget

Auth via `X-Internal-Api-Key: <faces_token>`.

## Notes
- Enrollment needs a clear, front-facing face in frame; the adapter rejects
  poor captures and the agent relays that ("couldn't enroll … no face").
- A "notify me about unknown faces" watch is a natural follow-up: a monitor
  mode that runs recognition each poll and alerts on an unrecognised face.
  Not built yet — it depends on the InsightFace adapter being registered.

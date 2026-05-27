# Demo Video — Production Script

Internal production document. The video itself is the single most
important launch artefact; everything else points at it. This page is
written to be executable — read top to bottom and you should be able
to record without further design work.

## Goals (ranked by priority)

1. **Make the camera-agent's WOW moment visceral.** "I asked my camera
   out loud and it answered" is the headline. Show, don't tell.
2. **Establish credibility without leaking enterprise-pitch energy.** A
   defence buyer and a homelab user both watch this video. The first
   needs to take the project seriously; the second needs to not feel
   talked down to. Honest tone wins both.
3. **Show the local-only / no-cloud claim physically.** Pull the network
   cable. Demonstrate the agent still works. This is the single most
   credible thing we can do in 60 seconds.
4. **Keep total length under the audience's tolerance.** HN: 30 seconds.
   Twitter / Mastodon: 60-90 seconds. YouTube / sales: 3-5 minutes. The
   30-second cut should stand alone; the longer cuts are extensions, not
   prerequisites.

## What this video is NOT

- Not a feature tour of the web UI (save that for separate docs videos).
- Not a tutorial on installation (the README covers it).
- Not a Powerpoint pitch with voiceover (HN will roast this).
- Not a polished broadcast production (over-editing reads as marketing
  and triggers immune response). Aim for "good enough that the WOW
  shines through" — not Apple-keynote production values.

---

## Pre-production checklist

### Environment

- **Physical scene.** Choose a camera and a scene that's interesting
  but not staged. A front door with packages on the step. A driveway
  with a parked car. An office hallway with a person walking through.
  Avoid empty rooms (boring), avoid blue-screen-style setups (looks
  fake).
- **Lighting.** Daylight or even artificial. The camera needs to
  produce a frame YOLOv8 can actually detect things in — low-light
  shots make the model look bad, which makes the project look bad.
- **Camera quality.** 1080p H.264 ONVIF camera is the right baseline.
  Anything cheaper makes detections noisy. Anything fancier looks
  like cherry-picking.
- **Audio environment.** Quiet room for the question. Whisper STT
  performs well in modest noise but the audience can hear background
  noise even when STT handles it fine, and they'll judge.

### Gear

- **Recording mic.** Use a decent USB or XLR mic for the spoken
  question, not the laptop's built-in. A Yeti, Shure MV7, or
  equivalent. The agent's TTS voice is clean; the human voice asking
  the question needs to match or it sounds amateurish.
- **Screen capture.** OBS Studio is free and standard. Capture at
  1080p / 30fps minimum, 60fps if you can stand the file size. Don't
  capture above your display's native resolution.
- **Camera-side capture (optional but ideal).** If you're showing the
  real-world scene, capture from a second physical camera so the video
  can cut between (a) you in front of the IP camera and (b) the
  screen showing the agent's response. Phone camera on a tripod is
  fine.
- **No DSLR-style depth-of-field bokeh.** Looks like an ad. Plain
  composition wins.

### Software setup

- **Browser.** Clean profile or new browser window. No bookmark bar,
  no tab clutter, no notification badges. Full-screen the
  `http://localhost:9100/demo` page.
- **OS chrome.** Hide the dock / taskbar where possible. macOS:
  `defaults write com.apple.dock autohide -bool true && killall Dock`.
  Linux: full-screen the browser via F11.
- **Notifications off.** System Do Not Disturb on. Don't ship a video
  where a Slack notification slides in mid-take.
- **Time of day.** Recording at night and saying "is there a person at
  the front door" reads as fake. Record at the time of day the scene
  matches.

### Test runs

- **Do five throwaway recordings first.** Latency from voice to TTS
  reply is the biggest unknown. Whisper STT on CPU is ~1.5-3s, Ollama
  with llama3.2:3b is ~3-8s depending on tool calls, Piper TTS is
  ~0.5s. Total round-trip is 5-12 seconds for the first question.
  Don't edit this out — frame it as natural pause. But know the
  number so you don't look surprised.
- **Test the tool calls actually work end-to-end.** If YOLOv8 has no
  weights or InsightFace fails to load, the agent gives a wrong /
  apologetic answer. Run the full Tier 0 + camera-agent stack ahead
  of recording.
- **Test the network-cable pull.** The cable-pull moment depends on
  the demo box being literally offline — make sure removing the
  ethernet cable doesn't break local routing between the containers.
  Docker bridge networks work fine here; host-network mode requires
  a slightly different setup (the host's loopback is fine but
  external `mediamtx:8554` resolution needs DNS that survives the
  unplug). Test before filming.

---

## 30-second HN cut (the headline)

This is the version embedded in the Show HN post. Stands alone.
No music. Minimal title cards.

### Beat-by-beat

| Time | What's on screen | Audio |
|---|---|---|
| 0:00–0:02 | Wide shot: person in front of an IP camera mounted at a doorway, or a clear scene in view. Subtle text overlay (lower third): "OpenNVR — self-hosted NVR with a voice agent" | Ambient room tone only |
| 0:02–0:05 | Hand reaches toward laptop / phone; clicks "Start" on the `localhost:9100/demo` page (visible briefly). | Soft click of the button |
| 0:05–0:10 | Cut to person speaking, framed naturally (mid-shot, no extreme close-up). They speak conversationally: | **Person:** "Is there a person at the front door?" |
| 0:10–0:14 | Cut to screen: agent's status indicator shows "Listening… processing…" (or whatever the actual UI shows). Hold on the screen briefly. | Brief silence — this is the round-trip latency. Don't fill it. |
| 0:14–0:20 | Screen shows YOLOv8 bounding box overlay on the live frame (person detected). Agent's reply renders. | **Agent (Piper TTS):** "Yes — there's one person standing at the front porch right now." |
| 0:20–0:25 | Slow zoom or pan on the laptop screen as a network cable is visibly pulled from the side. Text overlay: "No cloud. Local only." | Soft cable-pull sound (or no sound, just visual) |
| 0:25–0:28 | Repeat the question with the cable still out. | **Person:** "And is anyone in the kitchen?" |
| 0:28–0:30 | Agent answers correctly from the kitchen camera. Final overlay: "github.com/open-nvr/open-nvr" | **Agent:** "No — the kitchen is empty." |

### Notes on the 30-second cut

- **No voiceover narration.** The diegetic question + answer carries
  the demo. Adding voiceover reads as a commercial.
- **One title card max.** The opening lower-third and the closing
  github URL are enough.
- **No music** for this version. Music for emotional manipulation; this
  demo wins on substance. Music optional for the longer cuts.
- **The cable pull is the emotional anchor.** If you only have time to
  rehearse one moment, rehearse this one. Visible cable, visible
  hand, audible click. The audience needs to know the device is
  actually disconnected, not pretending to be.
- **Captions burned in or as SRT.** HN viewers often watch with
  sound off (work / commute). Captions are non-negotiable. Use the
  exact words spoken — don't paraphrase.

---

## 90-second extended cut (Twitter / Mastodon / website hero)

Same opening hook (0:00-0:30 identical to the HN cut). Then continues:

### Additional beats

| Time | What's on screen | Audio |
|---|---|---|
| 0:30–0:35 | Smash cut to the OpenNVR web UI dashboard showing 4-6 camera tiles. Bounding boxes rendering live. | Optional ambient music starts here (low-key, instrumental, not stirring) |
| 0:35–0:45 | Quick montage: alerts panel showing recent inferences, audit log with correlation IDs, license-plate recognition example firing. Text overlay: "Open AI adapter contract. Object detection, license-plate OCR, face recognition, scene captioning, multi-object tracking, voice — all yours." | Brief on-screen narration via text |
| 0:45–0:60 | Pull back to a person typing in a terminal. Show the actual install: `git clone`, `cp .env.example .env`, `./scripts/generate-secrets.sh --write`, `docker compose -f docker-compose.tier0.yml up -d`. Speed-ramp the docker compose output. Text overlay: "5-minute install. Pre-built images." | Music continues |
| 0:60–0:75 | Cut to academic paper PDF on screen (Zenodo page or the PDF itself). Hold briefly on the title + author list + abstract. Text overlay: "Built on published research. DOI 10.5281/zenodo.17261761." | Music continues |
| 0:75–0:90 | Final card: OpenNVR logo / wordmark, github URL, "AGPL". Hold for 3 seconds. | Music tail |

### Notes on the 90-second cut

- **Music selection matters.** Avoid corporate stock music. Look for
  Creative Commons instrumental that suggests "competent and quiet"
  rather than "soaring innovation." Reference: think Pop OS install
  videos, not Cisco product launches.
- **Text overlays are full sentences, briefly.** Audiences read at
  ~200wpm — give them about 1 second per 3-4 words on screen.
- **The paper shot is critical.** Don't skip. Hold long enough to
  actually read the title. The paper is the legitimacy claim; flashing
  it on screen for half a second reads as decorative rather than
  evidential.
- **Speed-ramp the docker compose output.** Real install takes ~5
  minutes; the video shows ~10 seconds of accelerated terminal. Make
  sure the speed-ramp doesn't distort the actual commands — operators
  watching want to read them.

---

## 3-5 minute deep dive (YouTube / selfh.st feature / sales)

For a longer-form audience that's already interested. Structure
follows a problem → solution → demonstration → architecture arc.

### Outline

**Cold open (0:00–0:15).** Same 15 seconds as the HN cut's opening
hook (question + answer + cable pull). No preamble — earn the
viewer's attention before any titles.

**Title + framing (0:15–0:30).** Full-screen text or graphic: "OpenNVR
— the open-source NVR you can talk to." Narrator (recorded VO, not
text-to-speech) sets up the problem in one sentence: *"Surveillance
cameras are everywhere — and almost none of them let you talk to them
or trust them. We built one that does both."*

**Problem framing (0:30–1:00).** Brief montage of the threats the paper
documents: Mirai botnet headlines, the Verkada breach (use generic
press imagery, not their logo), CISA advisory screenshots blurred to
avoid trademark issues, the FCC Covered List page. VO: *"In the last
five years, IP cameras have become the largest single category of
abused IoT devices. The 2021 Verkada breach exposed 150,000 cameras
in one credential compromise. Most existing self-hosted alternatives
treat AI as a bolt-on and security as a checkbox. OpenNVR inverts
that."*

**Solution overview (1:00–1:45).** Walk through the three-tier
architecture using either a real animated diagram or a screen-recording
of the SECURITY_ARCHITECTURE.md page being scrolled. VO covers
isolated-camera-network → middleware → analytics, naming the audit
chain and the AI adapter contract as the two key differentiators.

**Demonstration (1:45–3:00).** Extended version of the camera-agent
demo. Multiple questions, show the BLIP scene-caption tool firing on
a complex scene ("describe what you see at the back gate"), show
license-plate recognition on a car arriving at the driveway, show the
alert firing into Home Assistant if you have HA set up. The point is
breadth: this isn't a one-trick voice agent.

**Architecture credibility (3:00–4:00).** Show the published paper
(PDF on screen, DOI cited). Show the COMPLIANCE.md page being
scrolled. Hold on the framework alignment table. Show the audit log
producing real entries. VO names the compliance frameworks
(CISA Secure-by-Design, NIST CSF 2.0, NIST AI RMF, ISO/IEC 27001,
ETSI EN 303 645, GDPR, DPDP).

**Install + close (4:00–4:30 or 4:45).** Same speed-ramped install
sequence as the 90-second cut. End on the GitHub URL, the paper DOI,
and the contact email for commercial inquiries.

### Production notes for the deep dive

- **Voiceover is required.** The 30-second and 90-second cuts can rely
  on diegetic audio + text overlays. A 3-5 minute piece needs a
  narrator or it gets dull.
- **Use the maintainer's actual voice if possible.** Stock VO talent
  sounds like marketing. The project author's voice (assuming it's
  reasonably clear) reads as authentic.
- **Pacing — never sit on a single shot for more than 8 seconds.**
  Cut between angles, shots, and screen contents at a steady rhythm.
  Long static holds kill engagement.
- **Closed captions / SRT mandatory.** YouTube auto-captions are
  inadequate for technical terms. Hand-correct or pay for human
  transcription.

---

## Shot list (asset capture checklist)

Before editing, collect these. Each is a separate file you can mix
and match across the three cuts.

### Live-action shots

| Asset | Description | Length |
|---|---|---|
| `front-door-camera-wide.mp4` | Wide shot of the IP camera mounted near a door, environment visible | 10s |
| `person-asks-question-1.mp4` | Mid-shot of the maintainer asking "Is there a person at the front door?" | 5s |
| `person-asks-question-2.mp4` | Mid-shot asking "And is anyone in the kitchen?" | 5s |
| `person-asks-question-3.mp4` | Mid-shot asking "Describe what you see at the back gate" (for deep dive only) | 5s |
| `cable-pull.mp4` | Hand pulling ethernet cable from the OpenNVR machine, audible click | 4s |
| `running-server-establishing.mp4` | Wide shot of the actual hardware (Pi 5 / NUC / mini-server) with status LEDs | 5s |

### Screen-capture shots

| Asset | Description | Length |
|---|---|---|
| `agent-demo-page-listening.mp4` | The `localhost:9100/demo` page in "listening" state | 6s |
| `agent-demo-page-answering.mp4` | Same page showing the agent's text reply rendering + Piper TTS playing | 10s |
| `dashboard-multi-camera.mp4` | OpenNVR web UI dashboard, 4-6 camera tiles with live bounding boxes | 15s |
| `alerts-panel-firing.mp4` | Alerts panel as a license-plate-recognition or intrusion-detection event fires | 8s |
| `audit-log-correlation-id.mp4` | Audit log view showing a correlation_id-joined alert trail | 10s |
| `install-speed-ramped.mp4` | Terminal session of the Tier 0 install commands, sped 4x | 15s |
| `paper-pdf.mp4` | The architecture paper PDF, slowly scrolling through the title and abstract | 10s |
| `compliance-mapping-scroll.mp4` | Slow scroll through `docs/COMPLIANCE.md` showing the paper § → control → code mapping | 12s |
| `architecture-diagram.mp4` | Animated or static three-tier architecture diagram (build in Figma / Excalidraw) | 10s |

### Reference clips (for the deep dive's problem section)

These are external assets — make sure to use only Creative Commons
or fair-use / press-imagery-allowed sources. Don't use Verkada's,
Hikvision's, etc. trademarks or logos.

| Asset | Source | Length |
|---|---|---|
| Mirai-related news header (blurred or generic) | Press archive, fair use | 2s |
| Verkada breach news header (generic) | Press archive, fair use | 2s |
| CISA advisory screenshot (cisa.gov is public domain) | https://www.cisa.gov | 3s |
| FCC Covered List page | https://www.fcc.gov/supplychain/coveredlist (public domain) | 3s |

---

## Stills + GIFs

The video isn't the only visual artefact. Capture these alongside:

### Stills (for README, docs, social cards)

1. **README hero still.** Person speaking near a camera, OpenNVR
   dashboard visible on a screen behind. 16:9 or 4:3. ~1920×1080
   resolution. PNG.
2. **Web UI dashboard screenshot.** Clean state, real cameras showing,
   bounding boxes rendering. For the homepage and the DOCKER_QUICKSTART
   doc.
3. **Alerts panel screenshot.** Three or four representative alerts
   visible (intrusion, license plate, smart-doorbell unknown face).
4. **Audit log screenshot.** Showing correlation_id, model_fingerprint,
   sovereignty status. Demonstrates the audit chain visually.
5. **Demo page screenshot (mid-conversation).** The browser at
   `localhost:9100/demo` with a user question and the agent's reply
   visible.
6. **Architecture diagram** (still). Three-tier model rendered cleanly
   for procurement decks and the deep-dive video.

### Animated GIFs (for README + Twitter / Mastodon image cards)

GIFs should be small (< 5 MB ideally, hard cap 10 MB for Twitter), look
good at thumbnail size, and loop seamlessly.

1. **`camera-agent-hero.gif`** — 5-8 second loop of someone asking a
   question and the agent answering. README hero. Single most-viewed
   GIF in the project's lifetime.
2. **`detection-overlay.gif`** — 5 seconds of live YOLOv8 bounding
   boxes appearing over a scene as a person walks through. Lower-
   impact than the voice GIF; useful for places where audio context
   isn't available.
3. **`audit-log-stream.gif`** — 5 seconds of the audit log scrolling
   as events fire. For the SECURITY_ARCHITECTURE / COMPLIANCE pages.
   Niche but right for the procurement audience.

### Hosting

- **GIFs** go in the repo at `docs/assets/` or equivalent. Inline
  reference from README.
- **Videos** go on YouTube (public, unlisted for the deep dive until
  ready). YouTube also gets the SRT file uploaded; auto-captions are
  not adequate.
- **Backup hosting** for the HN cut: also upload to your own static
  hosting / S3 with a direct mp4 link. HN sometimes prefers direct
  links over YouTube embeds; some commenters will paste a YouTube
  link as a complaint.

---

## Editing notes

### Software
- **Video editor.** DaVinci Resolve (free, professional-grade) or
  Final Cut / Premiere if you already know them. Avoid iMovie for
  anything > 30 seconds — it gets in the way.
- **Captions / subtitles.** Generate from a transcript, hand-correct,
  burn-in for social media (so they show even on muted autoplay) +
  ship as separate SRT for YouTube and accessibility.

### Audio
- **Normalize to -16 LUFS** for spoken segments (industry standard for
  podcast-style content). YouTube's algorithm normalises uploads to
  about this level; matching it avoids the "this video is too quiet"
  / "this video is too loud" comments.
- **Speech compression at 2:1 or 3:1.** Smooths variations between
  the human voice and the Piper TTS voice. Without this the agent's
  TTS sounds noticeably "thinner" than the human speaker.

### Music
- **Where to source.** Epidemic Sound, Artlist, or Musicbed for
  professional content; YouTube Audio Library or Free Music Archive
  for budget. Avoid anything that sounds like a startup ad.
- **Where to skip.** The 30-second HN cut is better without music.
  Diegetic-only.

### Colour + look
- **Don't over-grade.** A subtle warm or neutral grade is fine; full
  cinematic LUTs read as production-overkill and damage credibility.
- **Match exposure across shots.** If you cut from a daylight outdoor
  scene to a fluorescent-lit indoor scene, the white balance shift
  is jarring. Either grade them consistent or use a brief title-card
  to motivate the cut.

### Export
- **HN / Twitter / Mastodon cut.** 1080p H.264, 30fps. Under 50 MB
  for HN compatibility, under 512 MB for Twitter, under 200 MB for
  Mastodon.
- **YouTube deep dive.** 1080p H.264 or 1440p if you've captured at
  that. 60fps optional. Whatever YouTube will accept (huge tolerance).
- **GIF exports.** Use Gifski or `ffmpeg -filter_complex
  "fps=15,scale=720:-1:flags=lanczos,palettegen"` two-pass for
  quality. 15-20 fps is plenty.

---

## Distribution checklist (after recording)

- [ ] HN cut: edited, captioned, exported, hosted on direct URL + YouTube
- [ ] HN cut: tested at thumbnail size — does the camera-agent moment read at 200×120 px?
- [ ] Extended social cut: same checks
- [ ] Deep dive: edited, captioned, uploaded to YouTube as unlisted, reviewed by at least one outside person before publishing public
- [ ] Hero GIF: under 5 MB, loops seamlessly, looks decent at 600px width
- [ ] All stills: archived in `docs/assets/` with descriptive filenames
- [ ] SRT files: published alongside each video for the deaf / hard-of-hearing audience and for search-engine indexing
- [ ] Backup hosting: HN cut available at a non-YouTube URL too

---

## Failure modes to watch for

A short list of ways the video can go wrong, sorted by how badly:

1. **The agent answers wrong.** YOLOv8 misses the person at the door,
   or InsightFace recognises the wrong family member. If this happens
   in your take, re-record. Don't ship "the agent confidently makes
   things up" as the launch demo.
2. **Latency makes the demo feel broken.** 5-12 seconds of round-trip
   silence is real but can read as a crash. Mitigate by showing the
   "Listening…" status indicator, not a dead screen. If the latency
   ever exceeds 15 seconds, something's wrong with the stack — debug
   before you re-shoot.
3. **The cable-pull moment doesn't read.** If the camera angle hides
   the actual cable, or the cut is too fast for the audience to see
   it happen, the "local-only" claim doesn't land. Rehearse this
   specifically; consider a brief slow-mo on the cable pull.
4. **The video looks like a corporate ad.** Avoid: lower-third graphic
   packages with kinetic typography, stirring orchestral music,
   smiling-stock-people-in-suits cutaways, "as featured in"
   logo bars. The audience for this demo would rather watch a slightly
   rougher authentic recording than a polished marketing piece.
5. **The narrator sounds nervous or rushed.** Better to delay shipping
   the deep-dive cut by a week and re-record than to ship a take
   where the VO sounds anxious. The viewer's trust calibration is
   set by the narrator's voice; if it cracks, so does the project's
   perceived seriousness.

---

## Owners + sign-off

This is internal. Update with names + dates as roles are assigned:

- **Director / on-camera presenter:** _____
- **Audio engineer / VO recordist:** _____
- **Editor:** _____
- **Captions QA:** _____
- **Final approver before HN post:** _____

Target ship date for the 30-second HN cut: end of week 2 per the
[`GTM_PLAN.md`](GTM_PLAN.md) timing.

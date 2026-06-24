# Transmit.Live — Research & Product Teardown

> Base research for the Instream-Overlay project. Compiled 2026-06-24.
> Primary source `https://www.transmit.live/` is blocked at the network-policy
> level in this environment (HTTP 403 on CONNECT), so the findings below were
> assembled from web search summaries of Transmit's own pages plus third-party
> coverage (PR Newswire, TV Tech, StreamTV Insider, Crunchbase, SVG). Links are
> listed in the Sources section.

---

## 1. One-line summary

Transmit.Live is a **live-streaming ad-monetization platform** that uses AI
("MomentAI") to detect high-value moments in live video (especially live
sports) and insert **non-disruptive, in-stream ad formats** — most notably a
**picture-in-picture (PIP) overlay** — that run *alongside* the content instead
of cutting away to a traditional ad break. The goal: create net-new premium ad
inventory and lift fill rates / CPMs without adding ad time or hurting the
viewing experience.

So the "in-stream overlay" in this context is **an advertising overlay** (brand
graphics / PIP video that sits over live content during a key moment), not a
streamer's cosmetic Twitch-style overlay.

---

## 2. Company background

| Item | Detail |
|------|--------|
| Founded | 2016 |
| HQ | New York City |
| Founders | Seth Hittman (Co-Founder & CEO), Duke Barnett, Scott Young (Co-Founder & CPO) |
| Stage | Series A |
| Funding | ~$7.82M total (reported, 1 round, 4 investors per Crunchbase/Tracxn) |
| Focus | Monetization of the world's most valuable live streamed content (sports, entertainment) |

**Scott Young (CPO)** — 20+ yrs in video tech; previously built revenue/product
at SET Media (acquired by Conversant/Epsilon, 2014), led video at Alloy and
video-ad platform YuMe (pre-2013 IPO), and helped CBS Interactive launch
streaming on-demand.

Notable milestone: Transmit positioned itself as **"OTT's first fully automated
ad break and content monetization for live sports and entertainment"** (PR
Newswire, 2021).

---

## 3. The platform: MomentAI™

MomentAI is the core engine. It **continuously analyzes live content** to detect
monetizable moments and then triggers ad decisioning + insertion automatically.

**What it detects:**
- Emotional peaks
- Game-changing plays
- Cultural inflection points
- Contextual signals

**What it does with a detected moment:**
1. Creates **premium ad inventory inside the moment** (increasing yield without
   extending ad time).
2. **Auto-clips highlights**, packages them with sponsor creative, and pushes
   them across the network — creating *new* inventory from each key moment.
3. Combines detection with **SSAI (Server-Side Ad Insertion)** to deliver
   hyper-contextual ads at scale.

**Claimed result:** proprietary in-stream ad formats unlock an **average ~30%
more inventory per event**.

Key framing they use: *detection → decisioning → automated execution*, all
re-architected around live (vs. VOD) monetization.

---

## 4. Products

### 4.1 Stream Composer
The content/ad management product. Lets content owners and advertisers
**manage streams, create inventory, and serve ads**.

Supported ad formats include:
- Picture-in-picture (PIP) video
- Pre-roll
- Mid-roll
- **Graphic overlays**

Customizable key-moment timing lets rights owners tune when ads appear to
maximize engagement. (Has a "Stream Composer Addendum" legal/spec doc.)

### 4.2 Stream Extension
Transmit's proprietary tech for **designing, creating and serving "Live
Promotions"** — ads that end users can **engage with in real time via
roll-overs and/or click-throughs** (i.e. interactive in-stream ad units).

Includes **creative services**: ad creative development, templates,
storyboards, production specs, and support. Aimed at maximizing fill rates and
premium inventory while keeping an optimal viewing experience. (Has a "Stream
Extension Addendum" doc.)

### 4.3 Demand Marketplace
The **transaction layer** connecting advertisers to the premium inventory
MomentAI identifies. Described as an automated, near-real-time exchange:
- Brand agents signal targeting requirements + creative preferences into the
  marketplace.
- The system scans thousands of live streams, finds high-attention moments, and
  **builds custom inventory matching brand specs**.
- Pricing, format, impression volume, and estimated KPI achievement are
  **negotiated in milliseconds**; deals form and ad units activate inside the
  live session instantly.

---

## 5. The hero ad format: Picture-in-Picture (PIP) / In-stream overlay

This is the most relevant piece for our project.

- Transmit bills itself as the **first in-stream video advertising platform that
  programmatically delivers addressable ads via a picture-in-picture format.**
- PIP shows **brand messaging/ads alongside content** instead of an ad break
  that cuts away — viewer never leaves the content.
- **PIP ad pods** can cycle through multiple ad spots without breaking away.
- Can be **supplemental** to existing breaks **or replace** them.
- The AI engine detects precise events to create **non-disruptive sponsored
  moments**.
- Outcome claimed: higher engagement for brands + incremental revenue for
  publishers + better audience experience (less bounce, more retention).

**Demo page:** `https://www.transmit.live/demos/news/overlay` (a "news" overlay
demo — blocked here, worth viewing in a normal browser to study the actual UI,
animation, and placement).

---

## 6. Solutions (target segments)

| Segment | Pitch |
|---------|-------|
| **FAST channels** | AI monetization that creates premium inventory inside content, boosting fill rates & CPMs without adding ad breaks. Marketplace matches AI-detected moments to brand buyers. |
| **DTC (direct-to-consumer)** | Contextual, non-disruptive ad formats that reduce bounce and keep viewers watching. |
| **Brands** | Use the AI platform to manage streams, create inventory, and serve ads; proprietary in-stream formats unlock incremental net-new revenue via the exclusive demand marketplace. |

---

## 7. Partnerships & traction

- **Wurl** (Nov 2024) — Transmit brought its in-stream / PIP CTV ad formats to
  **thousands of Wurl-powered FAST channels**, letting them create incremental
  inventory & revenue without disrupting viewers. (Covered by TV Tech &
  StreamTV Insider.)
- **ViewLift** (Dec 2024) — partnership to maximize placement and monetization
  of ads in digital feeds.
- Active in the **live sports** monetization space (SVG sponsor; "The golden age
  of sports is now," Feb 2025).

---

## 8. Technical concepts to know (glossary for our build)

- **SSAI (Server-Side Ad Insertion):** ads stitched into the video stream
  server-side so they're part of the manifest/segments — harder to block, no
  buffering on ad transitions, enables per-viewer addressable targeting using
  non-cookie signals (IP, device, content context). Contrast: CSAI
  (client-side).
- **SGAI (Server-Guided Ad Insertion):** a newer middle-ground where the server
  signals the client when/what to insert.
- **PIP ad pod:** a sequence of ad spots cycled inside a small overlay window
  while main content keeps playing.
- **FAST:** Free Ad-Supported Streaming TV (linear-style, ad-funded channels).
- **Addressable inventory:** ad slots that can be targeted/personalized per
  viewer or per moment.

---

## 9. Where this leaves *our* project (Instream-Overlay)

The interesting, buildable core of what Transmit does — and a natural starting
scope for us — is the **in-stream overlay ad unit** itself:

1. A **video player** with a content stream.
2. An **overlay layer** that can render a PIP ad window + graphic overlays on
   top of live content, triggered at specific timestamps/"moments."
3. A **moment/trigger system** — start simple (manual/timed cue points or
   metadata markers), with a path toward automated "MomentAI"-style detection.
4. **Interactive ad units** (roll-over / click-through — Transmit's "Live
   Promotions").
5. (Later) an **ad-decisioning / marketplace** layer and **SSAI** integration.

A sensible MVP: render a non-disruptive PIP/graphic overlay over an HLS live
stream, driven by timed cue points, with click-through interactivity — then
layer detection and serving logic on top.

---

## 10. Open items / to verify in a real browser

The following pages are blocked in this environment and should be reviewed
directly to capture exact copy, UI, and the live overlay demo behavior:

- `https://www.transmit.live/` (home)
- `https://www.transmit.live/momentai` (platform)
- `https://www.transmit.live/stream-composer`
- `https://www.transmit.live/demos/news/overlay` (the actual overlay demo)
- `https://www.transmit.live/solutions/for-fast-channels`
- `https://www.transmit.live/solutions/for-dtc`
- `https://www.transmit.live/solutions/for-brands`
- `https://www.transmit.live/press` and `/blog`
- Docs: `https://info.transmit.live/docs/stream-composer-addendum`,
  `https://info.transmit.live/docs/stream-extension-addendum`

---

## Sources

- [Transmit home](https://www.transmit.live/)
- [Platform / MomentAI](https://www.transmit.live/momentai)
- [Stream Composer](https://www.transmit.live/stream-composer)
- [Overlay demo](https://www.transmit.live/demos/news/overlay)
- [For FAST Channels](https://www.transmit.live/solutions/for-fast-channels)
- [For DTC](https://www.transmit.live/solutions/for-dtc)
- [For Brands](https://www.transmit.live/solutions/for-brands)
- [Stream Composer Addendum](https://info.transmit.live/docs/stream-composer-addendum)
- [Stream Extension Addendum](https://info.transmit.live/docs/stream-extension-addendum)
- [PR Newswire: OTT's first fully automated ad break / content monetization](https://www.prnewswire.com/news-releases/transmit-introduces-otts-first-fully-automated-ad-break-and-content-monetization-for-live-sports-and-entertainment-301354044.html)
- [TV Tech: Transmit, Wurl Partner on FAST Channel Ads](https://www.tvtechnology.com/news/transmit-wurl-partner-on-fast-channel-ads)
- [StreamTV Insider: Wurl boosts FAST arsenal with in-stream CTV ad formats from Transmit](https://www.streamtvinsider.com/advertising/wurl-boosts-fast-arsenal-stream-ctv-ad-formats-transmit)
- [Crunchbase: Transmit.Live](https://www.crunchbase.com/organization/transmit-live)
- [Crunchbase: Scott Young](https://www.crunchbase.com/person/scott-young-2)
- [Tracxn: Transmit.Live profile](https://tracxn.com/d/companies/transmit.live)
- [SVG: Scott Young on monetizing live streaming sports](https://www.sportsvideo.org/2022/08/16/svg-new-sponsor-spotlight-transmits-co-founder-scott-young-on-effectively-monetizing-live-streaming-sports-content/)
</content>
</invoke>

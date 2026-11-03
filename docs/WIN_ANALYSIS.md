# Can Reflex win? A research-backed answer

Written November 1, 2026, from four parallel investigations: what has won Cerebral Valley and Meta hackathons since 2024, the prior art a robotics judge will name, what the competition has posted publicly, and how the idea sits with Meta's own bets. Sources are at the end. Where a fact is secondary or self-reported, it says so.

---

## 1. The answer

**Is Reflex the absolute best idea to win? No idea is.** The question that can be answered is whether it is the best bet for this kit, this field and this builder, and the evidence says **yes, provided it is executed like the software entries that actually win, not like the hardware entries that usually lose.** Six changes below follow from the research, and two of them matter more than anything in the concept itself.

The honest odds, after those changes and a working arm on video: a real shot at the indie top five (reportedly $150K / $100K / $75K / $50K / $25K), and a first place that depends on the video being the best hardware video in a pool of several thousand. Without a working arm on video, nothing places, whatever the idea.

---

## 2. What the research found

### 2.1 Hardware almost never wins video-first hackathons

Across roughly thirty placed or finalist projects at Cerebral Valley events with Anthropic, Meta, Mistral and Google since 2024, physical hardware took one first place (OpenGlass, $20 smart glasses, Meta Llama 3 hackathon, May 2024) and robotics placed once (Gripmind, EEG and voice to Llama Vision to a robot arm for assisted living, 2nd, Llama Impact London, Nov 2024). Both were live, in-person demos. In the two virtual, video-first Anthropic hackathons (twelve awards), no robotics project won. This hackathon is virtual and video-first.

Why this matters: in a room, a moving arm is magnetic. In a first-pass review of thousands of three-minute videos, it is another video. The arm earns nothing by existing; only what it visibly does for a person earns anything.

### 2.2 Winners share a shape, and Reflex is missing one part of it

The winners of the sibling Anthropic events and the Meta events were, overwhelmingly: a person with domain knowledge, solving a narrow and legible problem, with a before-and-after number in the first minute. A lawyer on permit letters (weeks to fifteen minutes). A cardiologist on post-visit notes. A repair technician on board schematics. Healthcare took about a fifth of top-three slots and two of nine first places. Anthropic's own framing: "four of the five winners were not professional developers."

Reflex has the narrow problem and the number. It does not have the domain insider. Nobody on the team is a clinician, therapist or disabled user. That gap is closable in one afternoon (§3, change 2) and it is the single most important thing the research says to do.

### 2.3 The reported rubric

Two secondary sources quoting an update to the rules agree on Impact 30%, creative use of Meta's AI tooling 25%, demo including video 25%, technical depth and execution 20%. The sibling Anthropic rubric was the same four items with the first two swapped, which makes the report plausible. Prizes: indie $400K across five places, startup $550K across five, up to $50K in bonuses (community vote, "most viral" are mentioned).

A participant summary, matching the Anthropic rules word for word, says the demo should show what was built during the event and that an unclear contribution can disqualify, plus a pass/fail gate on theme fit and genuine Meta model use. The Official Rules were checked against the entry on November 1 and it qualifies. The practical consequence is presentational: the README carries a dated list of what was built, and the video shows those features.

### 2.4 Every component has prior art; the combination does not

A robotics judge will name these within seconds: CASPER (CoRL 2025: a VLM infers intent during teleoperation and proposes one action, confirmed by a keypress; 13 able-bodied users), DLR's EDAN (an EMG-controlled wheelchair arm for people with muscular atrophy, 2020–2025), Naughton and Hauser (RA-L 2022: a ranked top-k next-action menu "like predictive text"), Argall's lab (interface-aware shared control with eight spinal-cord-injury users on a sip-and-puff), Dragan and Srinivasa's policy blending, Admoni's gaze work (gaze alone is a weak intent signal), and co-contraction as the standard myoelectric mode switch.

What no search found: the one-muscle vocabulary where one channel gives twitch-accept, sustained-brace-refuse and brace-during-motion-freeze; an LLM that may only make a robot more conservative, from video incident review, after human approval; and any published selections-per-task number for a switch user on an arm task against scanning. Those three are the honest novelty claims. Nothing broader survives.

The scanning baseline is well documented and should be quoted: row-column scanning averaged 4.9 selections per minute at 2.2 switch hits per selection in a CHI 2022 study; rates of one word per minute "and lower are common" for motor-impaired users. A one-twitch, six-second cup is a large and defensible gain against that.

### 2.5 The competition is thin where Reflex stands

Of about 400 public GitHub repos referencing the hackathon (a lower bound; many stay private), roughly a dozen touch real hardware and about six have a physical robot arm: three SO-101 bench assistants with safety layers (Workbench Co-Pilot, Say-and-Grasp, Pathfinder), one small industrial sorting cell with measured results, one DOFBOT, one LEGO. Only two report measured real-arm results, and none had a finished demo video as of November 1. Fifteen to twenty projects are assistive, nearly all camera-to-speech for blind users. **Zero combine accessibility with a physical robot. Zero use EMG. Zero learn anything from a user's reaction.**

The nearest neighbour in spirit is Ventriloquist: a yes/no communication aid for ventilated ICU patients that tracks a calibrated eyebrow movement with SAM 3.1 and uses Muse Spark to propose questions by information gain. It has a three-minute film, a live app, 98 commits, a fifteen-rule safety table and simulated results. It is the polish bar for this niche, and it has no robot and no muscle signal. Reflex should be able to say in one line why EMG beats a camera for this population: it works with eyes closed, in the dark, with facial paralysis, and Meta's own research says muscle signals persist in people paralyzed for years.

### 2.6 Meta has already said the input half of this pitch

In official channels, three times (May 2025, January 2026, May 2026), Meta's accessibility team has said wrist surface EMG "can provide control signals even if someone can't produce large movements" and that its band "detects subtle muscle signals in the forearm, even in people who have been paralyzed for many years," with a three-year Carnegie Mellon partnership on spinal cord injury and a University of Utah study on ALS, stroke, muscular dystrophy and limb loss. Meta has never said sEMG should control a robot; its targets are screens, games, smart home and a mobility ski. Separately, Meta's August 2026 Muse Spark 1.2 post describes a robotics planner that "decomposes the goal into subtasks" executed by a low-level policy, the same split Reflex uses with a deterministic gate between.

Two rules follow. Cite Meta's sEMG accessibility work as validation of the signal, in one sentence. Never use the Neural Band name, hardware or reverse-engineered protocols; there is no official raw-EMG API and Meta states it does not support those paths.

---

## 3. Six changes that follow from the evidence

In order of how much they move the odds.

**1. Make the entry runnable without the arm.** The strongest entries in the pool all have a mock or replay mode that runs with no hardware and no API key, a hosted software demo, and a results table with small n. A first-pass judge who can open a page, scrub a recorded incident with its EMG trace, and watch the virtual arm take the long way round has experienced the product. One who can only watch a video has not. The virtual arm, synthetic EMG and recorder already exist; this is a day of UI work and it turns the hardware risk from fatal into survivable.

**2. Put a real voice in the video.** One occupational therapist, one person with a spinal cord injury or ALS, one caregiver, for twenty minutes, quoted with permission. The winners' pattern is domain insight, and this is the only way to borrow it honestly. If nobody can be reached by the deadline, say so on the end card; an honest gap reads better than an implied endorsement.

**3. Lead with the number, then the person, then the robot.** The first twenty seconds: a scanning menu counting selections and seconds for a cup, then Reflex doing it in one twitch. The published baseline (4.9 selections per minute, 2.2 hits each) on screen beside the measured one. The robot is the third thing a judge sees, not the first.

**4. Claim novelty only where it holds, and cite the rest.** One slide: "Builds on CASPER, EDAN, Argall and Naughton–Hauser. New here: the one-muscle vocabulary, restrict-only learning from video review, and the first selections-per-task measurement against scanning." Head direction is a tie-breaker for the ranker, never an intent sensor; Admoni's lab showed why.

**5. Show the fallback failing gracefully, on camera.** A 2026 "Most Creative" winner designed and demonstrated a rule-based fallback for when the API died; judges reward resilience they can see. Pull the network during a take: the three cards stay, scanning mode takes over, the twitch still works, the arm still stops. Ten seconds of video, and it answers the latency and reliability questions before they are asked.

**6. Keep the flinch as the fifteen-second clip.** The bonus prizes reportedly include a community vote and a "most viral" award. A person's shoulders jumping and an arm freezing mid-air, with the EMG trace spiking, is the only moment in the entry that works with no context in a feed. It is beat five of the main video and the whole of the social clip.

---

## 4. What would make it not the best bet

- **If the arm is not reliable on video by November 3**, the entry becomes the software story on the virtual arm, labelled as simulation. Change 1 makes this survivable; it does not make it a winner.
- **If a software-only pivot were on the table**, it would raise the floor and lower the ceiling. The pool's largest cluster is "photo to SAM to Spark to report," hundreds deep. Reflex competes in a cell with nobody in it. That is worth the risk.

---

## 5. Sources

Hackathon and judging
- Event page: https://cerebralvalley.ai/e/meta-ai-developer-hackathon
- Reported weights and prize split (secondary): https://madrobot.blog/2026/10/03/meta-global-ai-developer-hackathon-1-million-prizes-muse-model-api/ ; https://aimagazine.blog/meta-opens-its-1m-global-ai-developer-hackathon-20000-seats-150-api-credits-each-and-applications-close-october-25/
- Participant summary of rules (unverified): https://github.com/meta-ai-hackathon/meta-hack
- Sibling rubric and process, Built with Opus 4.6 (archived official rules): https://huggingface.co/datasets/introvoyz041/postvisit/raw/main/data/docs/hackathon-rules.md ; winners: https://claude.com/blog/meet-the-winners-of-our-built-with-opus-4-6-claude-code-hackathon ; https://www.claude.com/blog/meet-the-winners-of-built-with-opus-4-7-claude-code-hackathon
- Meta Llama 3 hackathon recap (OpenGlass): https://ai.meta.com/blog/llama-3-hackathon-recap-cerebral-valley/
- Llama Impact London (Gripmind): https://about.fb.com/news/2024/11/metas-llama-impact-hackathon-pioneering-ai-solutions-for-public-good/
- LlamaCon hackathon: https://ai.meta.com/blog/llamacon-hackathon/

Prior art
- Dragan & Srinivasa, policy blending, IJRR 2013: https://www.ri.cmu.edu/pub_files/2013/5/assistive_teleop_IJRR.pdf
- Liu et al., CASPER, CoRL 2025: https://arxiv.org/abs/2506.14727
- Vogel et al., EDAN, IROS 2020: https://elib.dlr.de/138661
- Nejati Javaremi, Loke, Argall, ISER 2023: https://www.argallab.northwestern.edu/files/2024/03/23iser_nejati-51fde4021668d9e2.pdf
- Herlant et al., mode switching cost, HRI 2016: https://personalrobotics.cs.washington.edu/publications//herlant2016modeswitching.pdf
- Naughton & Hauser, structured action prediction, RA-L 2022: https://par.nsf.gov/servlets/purl/10345591
- Aronson & Admoni, gaze complements control, RSS 2022: https://www.ri.cmu.edu/publications/eye-hand-behavior-human-robot-shared-manipulation
- Yang, Erickson, Weber et al., HD-EMG in-home manipulation, 2026: https://arxiv.org/abs/2602.02773
- FEAST, whitelisted LLM edits, 2025: https://arxiv.org/html/2506.14968v2
- Scanning baselines: Simpson, Mankowski, Koester 2011: https://benthamopenarchives.com/contents/pdf/TOREHJ/TOREHJ-4-1.pdf ; Bonaker et al., CHI 2022: https://par.nsf.gov/servlets/purl/10325857
- Salazar-Gomez et al., ErrP correction, ICRA 2017: https://dspace.mit.edu/handle/1721.1/137032
- Kirschner et al., Expectable Motion Unit, RA-L 2022: https://arxiv.org/abs/2109.07201

Competition (self-reported READMEs, Nov 1)
- Ventriloquist: https://github.com/0xNoramiya/ventriloquist
- Workbench Co-Pilot: https://github.com/raahimnawaz/workbench-copilot
- Say-and-Grasp: https://github.com/adoodevv/say-and-grasp
- Pathfinder: https://github.com/Liamostudio/Pathfinder
- muse-qc-cell: https://github.com/StefanRohringer/muse-qc-cell

Meta alignment
- Advancing Accessibility at Meta, May 2025: https://about.fb.com/news/2025/05/advancing-accessibility-meta/
- University of Utah Neural Band accessibility research, Jan 2026: https://www.meta.com/en-gb/blog/university-of-utah-accessibility-research-meta-neural-band/
- Meta AI wearables for disabled people, May 2026: https://about.fb.com/news/2026/05/meta-ai-wearables-changing-the-game-for-disabled-people/
- CMU–Meta sEMG accessibility partnership, 2024: https://www.cmu.edu/news/stories/archives/2024/july/cmu-meta-seek-to-make-computer-based-tasks-accessible-with-wristband-technology
- Kaifosh, Reardon et al., generic neuromotor interface, Nature 2025: https://repository.cshl.edu/id/eprint/42049/
- Multimodal intelligence of Muse Spark 1.2 (robotics planner), Aug 2026: https://research.meta.ai/blog/multimodal-intelligence-of-muse-spark-1-2
- Meta Model API models: https://dev.meta.ai/docs/models.md

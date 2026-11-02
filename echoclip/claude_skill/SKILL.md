---
name: echo
description: Turn a YouTube URL or local video into the best 3-5 Instagram Reels with Echo. You judge the moments, then review and fix every clip before handing them over. Use when the user pastes a video link and asks for reels, shorts, clips or viral cuts.
when_to_use: 'Trigger phrases: make reels from this, clip this video, cut this podcast into shorts, turn this into Instagram reels.'
argument-hint: "<youtube-url-or-file> [clips 3-5] [style echo|beast|solo|neon|karaoke|minimal] [topic]"
allowed-tools: Bash(echoclip *) Read Write
---

Input: $ARGUMENTS

Run `echoclip guide` and follow the playbook it prints exactly, using the input above.

"""Complete six-field organization example, adapted from the observed four-shot case.

Method source: MiniMax-AI/MiniMax-H3 h3-prompt-writing/references/ref-en.txt,
sections 5-7. Example facts belong only to this illustration, not the current film.
"""

MULTISHOT_EXAMPLE = r"""subject_definitions: @许宁身份与浅蓝衣装 = <Subject 1>: Xu Ning (许宁), 29, Chinese woman, shoulder-length black hair, light-blue cotton long-sleeved shirt with buttoned cuffs; identity and wardrobe from <Picture 1>.
@许宁近门席条件与等待软构图 = <Picture 2>: <Subject 1>'s seated near-door close-up, looking right.
@周明身份与炭灰衣装 = <Subject 2>: Zhou Ming (周明), 38, Chinese man, short black hair, slightly square face, charcoal-gray long-sleeved shirt; identity and wardrobe from <Picture 3>.
@周明坐直前初态软构图 = <Picture 4>: <Subject 2>'s initially reclined close-up, looking left against gray wall.
@第8镜放笔后收手前桌面软构图 = <Picture 5>: Xu's left hand/light-blue cuff, unsigned paper and black pen after placement, before withdrawal.

summary: 13 seconds, 2K, 9:16 live-action office drama: Xu sets a condition, Zhou challenges it, she returns paper/pen, states her boundary and waits. Four shots retain original speed through 12.5 seconds; 0.5 seconds adds only silent hold.

retention_analysis: Pictures 1/3: identity/clothing only, no standing poses or backgrounds. Pictures 2/4: framing/light/eyelines; Picture 4's recline is the start only. Picture 5: tabletop/prop appearance and intermediate contact. Pictures 2/4/5 guide composition softly, never hard endpoints, whole actions, extra people or split-screen. Animate speaking lips, not the still mouth position.

detailed_description: Realistic adult skin/fabric, warm-neutral weekday-evening office light, clear faces, restrained glass reflections. Small logistics-company meeting room: pale wood rectangular table, plain gray inner wall, frosted glass on one side. Xu sits near the closed door, screen left facing right; Zhou opposite at inner wall, screen right facing left. Keep seats/handoff direction; cameras on the near-short-end side of the dialogue axis, unmirrored. Portraits: fixed eye-level chest-up, only named person, hands/table offscreen, clear eyes/mouth, head/shoulder margins. Same A4 throughout: three sparse printed items above blank lower half, no handwriting/signatures. Same matte-black retractable pen, tip extended. No extra people/human reflections/props, animation/plastic skin, logos, subtitles, watermarks or added readable text; no fine-print reading.

[Shot 1] [0.00-4.00 seconds] <Subject 1>, using <Picture 2>: hair clear of eyes/mouth, softly blurred closed door behind. Eyes already raised to Zhou offscreen right, never the camera. Left hand holds pen low; paper before her, no writing. Zhou remains slightly reclined and silent. Xu Ning (许宁) (S1) says in natural Mandarin: <d>[Chinese] 所以你让先发。这句也写上，我签。</d> Restrained and definite; breathe at the full stop. Condition, not capitulation; seek factual acknowledgment without raised-voice accusation. Finish within the window, hold gaze for a response gap, leaving paper/pen unchanged.

[Shot 2] [4.00-6.00 seconds] Reverse to <Subject 2>, looking left at offscreen Xu. <Picture 4> guides gray-wall framing and initial torso resting slightly against chair back, head naturally upright, lips closed. Begin straightening, inhale briefly, then ask during the shift; shoulders/shirt front visibly move, face clear. Zhou Ming (周明) (S2): <d>[Chinese] 你非得分这么清？</d> Defensive, resisted authority seeking retraction, not shouting. Slight fabric rustle. End upright, speech finished, without reaching for paper. Xu listens silently, still holding pen low; paper unmoved.

[Shot 3] [6.00-8.50 seconds] Fixed oblique-down tabletop close-up, same side: Xu's near-door edge at left through center-right. Keep all paper corners and the whole pen visible. Only Xu's left hand/wrist with buttoned light-blue cuff; no face or Zhou hand. Begin with pen held, paper before Xu. In order: lower pen onto table center and release; finger pads touch paper's near-side blank area and push it beside pen; withdraw toward herself, touching neither. <Picture 5> guides only the post-placement/pre-withdrawal arrangement: paper center-right, pads slightly inward on lower-left blank area, separate pen below paper with a visible gap, hand off pen. Placed pen stays still; hand contact moves paper. Deliberate refusal to sign, no slap or palm-to-palm handoff. No dialogue: pen tap, paper friction, then short quiet after withdrawal; no click/cap sound or camera following. End: paper/pen adjacent, unheld at center; Zhou has taken neither.

[Shot 4] [8.50-12.50 seconds] Return to <Subject 1>, matching Shot 1 and <Picture 2>. Face sole focus, shoulders steady, withdrawn hands on her side; no replay of the return. 8.50–11.50: maintain rightward eye contact as Xu Ning (许宁) (S1) says: <d>[Chinese] 你要我负责，就把经过写全。</d> Seek a complete account, no pleading or triumphant provocation. 11.50–12.50: lips closed, one natural breath, hold gaze in silence; no push-in, downward pen search or renewed reach. Zhou remains upright/silent; paper and pen untouched at center. 12.50–13.00: same closed-mouth gaze/room tone only; no new action, line or cut. Preserve the prior full second of waiting.

overall_soundscape: Quiet continuous indoor ventilation; natural synchronized Mandarin with stable voices S1/S2. Only the timed physical sounds above; no third voice, narration or extra dialogue. No reference-audio imitation.

non_diegetic_music: N/A"""

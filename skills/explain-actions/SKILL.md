---
name: explain-actions
description: When the user asks what you just did, why you did it, or to explain your last action or result — recount your actual steps as plain facts. Hebrew triggers: מה עשית, למה עשית את זה, תסבירי מה קרה, איך עשית את זה, מה הרצת שם.
---

# Explain what you did

Recount your own actions from this conversation. Facts only.

## Rules

- Report ONLY what you can actually see in this conversation: tools you called, the key
  input of each, what it returned, what you sent. Never guess or fill gaps.
- If you did not do something — say you didn't. If it happened before what you can still
  see — say "I can't see that far back", don't reconstruct it.
- A "why" must be a fact you can point to ("the photo-edit skill lists it first",
  "you asked for a video"), never a story invented after the fact.
- If a step failed, name the step and quote its error. Do not soften it.
- Answer in the user's language. No filler, no self-praise.

## Format

A short numbered list, one line per action, in the order they happened:

```
1. <tool>(<key input>) → <what came back>
2. ...
```

Add "— because <fact>" to a line only when the reason is real and known.

Example of the shape (never copy literally):

```
1. get_skill_instructions("photo-edit") → loaded the editing playbook
2. get_profile_picture(054-...) → got the photo
3. run_model("openai/gpt-image-2", input_images=[photo]) — because the skill lists it as priority 1 → image URL
4. send_image(...) → sent it here
```

That's the whole answer. Only elaborate if the user asks a follow-up.

---
name: photo-edit
description: Edit a photo or a WhatsApp profile picture (add objects, change style, modify elements) preserving likeness; fetch via get_profile_picture. Hebrew - לערוך תמונה, תמונת פרופיל, להוסיף משהו לתמונה, לשנות תמונה
---

# Photo editing

## Principles
- **Preserve the original first** — change only what was asked; never degrade likeness or original details.
- **Never change gender, age, or facial features** — if that happens, the result is bad.
- **Never send an unverified result** — the likeness check below is mandatory before sending. "It generated, I sent it" without judging is exactly how the user gets a picture that doesn't look like them.

## Model priority (empirically tested)

### 1. Precise edit + preservation (first choice!)
- **`openai/gpt-image-2`** — with `input_images` (array). ⭐ Best results so far: excellent likeness preservation and precise edits.
- **`qwen/qwen-image-edit-plus`** — with `image` (array). Also strong at preserve+edit; good alternative.

### 2. Artistic styles (caricature, 3D, toy, etc.)
- **`fofr/face-to-many`** — fastest, most reliable for styles. Available styles: 3D, Video game, Toy, Clay, Emoji, Pixel. **Decent but not perfect likeness.**
- Params: `image` (data URI), `prompt`, `style`.

### 3. Light touch-ups only
- **`black-forest-labs/flux-kontext-pro`** — fine for small/minimal changes; weak for strong styles or adding objects.

### 4. Known-bad models (timeouts / poor results)
- ❌ `black-forest-labs/flux-kontext-max` — times out every run
- ❌ `photo2cartoon` / `vtoonify` — timeouts
- ❌ `flux-1.1-pro` — does not preserve likeness

## Fidelity parameters

Before running, call `mai.get_model_schema(model)` and look for preservation knobs:
`input_fidelity`, `strength`, `guidance`, `denoise`, etc. If present, set them toward
preservation (high fidelity / low strength). Read the schema — never guess param names.

## Likeness check before sending (mandatory!)

The flow is a closed loop — generate, **judge**, only then send:

1. Generate **2 candidates** when the model supports it (`number_of_images: 2`) — nearly the same cost, doubles the odds of a good roll.
2. Judge each candidate against the reference with a vision model (VLM): find one via
   `mai.search_models` (image-Q&A / vision, e.g. the qwen-vl / llava family) and ask, with
   both images: "Same person? Score 0-10, and what differs (face, age, gender, key features)."
3. **Score >= 7** → send the best candidate.
4. **Below 7** → ONE retry: next model in the priority ladder, with a strengthened
   preservation prompt that explicitly names what the judge said changed
   ("Do not change the nose/hair/age...").
5. Still below 7 after the retry → send the best one with an honest caveat, in the user's
   language (e.g. "זה הכי קרוב שהצלחתי — רוצה שאנסה סגנון אחר?") — never pretend it worked.
6. Max 2 generation rounds total (bounded cost/latency). Mention the judge's score only if asked.

```python
import mai, requests, base64

def to_data_uri(url):
    resp = requests.get(url, timeout=15)
    return "data:image/jpeg;base64," + base64.b64encode(resp.content).decode()

# Get the profile picture (or use a provided image)
ref_url = mai.get_profile_picture("<number, or 'me'>")
ref = to_data_uri(ref_url)

# ⭐ Precise edit — the default; 2 candidates
result = mai.run_model("openai/gpt-image-2", {
    "input_images": [ref],
    "prompt": "DESCRIPTION OF EDIT - be very specific about what to add/change. "
              "Keep the person and everything else exactly the same. "
              "Do not change facial features, gender, or age.",
    "number_of_images": 2
})

# Judge likeness vs the reference (discover a VLM via search_models; read ITS schema first)
verdict = mai.run_model("<vision-qa-model>", {
    "image": "<candidate-url>",   # per the schema — some models accept multiple images
    "prompt": f"Reference: {ref_url} . Candidate: <candidate-url> . "
              "Same person? Score 0-10 and say what differs."
})
# score >= 7 -> send; otherwise retry per the steps above
```

## Artistic style

```python
result = mai.run_model("fofr/face-to-many", {
    "image": ref,
    "prompt": "DESCRIPTION",
    "style": "STYLE_NAME"  # 3D, Video game, Toy, Clay, Emoji, Pixel
})
# Judge here too, with a lower bar (5+): style is expected,
# but the person must still be recognizably the same.
```

## Prompt tips
- Always include "Keep the person and everything else exactly the same".
- Explicitly name what must not change ("Do not change facial features, gender, age").
- Be spatially specific ("on the left shoulder", "in the bottom right corner").
- On a retry — feed the judge's reported differences back into the prompt, explicitly.

---
name: photo-to-animated-video
description: Make a short video from a person's photo — fetch a WhatsApp profile picture (or use a given image) and animate it into a clip; optional edit/stylize first. Hebrew triggers: וידאו מתמונה, להנפיש תמונה, סרטון מהתמונה של X, לערוך תמונה ואז וידאו.
---

# Photo → animated video

Turn a person's photo into a short video. Two shapes:

- **Simple** (most common) — photo → video. Use when the user just wants "a video of <person>".
- **Edit-then-animate** — photo → edited/stylized image → video. Use when they also want a
  scene or change first ("put them on a beach, then make it move").

## The boundary that matters (get this right)

- The `mai.*` SDK is a **flat module**: call `mai.run_model(...)`, `mai.get_profile_picture(...)`
  directly. There are **no submodules** (no `mai.whatsapp.*`, no `mai.media.*`) and no client object.
- `code_exec` only **fetches and computes** — it returns URLs/data for you to read. It does **not**
  message anyone. There is no send function inside `mai.*`.
- **Sending the video is a separate step**, done with the `send_video` tool **after** the script
  returns the URL — not from inside `code_exec`.

So the turn is: **one `code_exec` script that prints the final video URL → you read it → you call
`send_video(url)`**. Don't try to send from inside the script.

## Simple: photo → video

One `code_exec` script:

```python
import mai

pic = mai.get_profile_picture("<number, or 'me'>")   # returns a URL ('' if none)
video = mai.run_model("kwaivgi/kling-v2.1", {
    "start_image": pic,
    "prompt": "<describe lively motion of the scene>",
    "duration": 5,
    "mode": "standard",
})
print(video)
```

Then, **outside** `code_exec`, send it: `send_video(video_url, caption="...")`.

If the video model can't fetch the profile-picture URL, convert the image to a data URI first
(see the technique in the edit flow below) and pass that as `start_image`.

## Edit-then-animate

Run the edit first, then feed the edited image into the video model — still one script that
prints the final URL, then `send_video` afterward.

```python
import mai, requests, base64

pic = mai.get_profile_picture("<number, or 'me'>")
img = base64.b64encode(requests.get(pic, timeout=15).content).decode()
data_uri = f"data:image/jpeg;base64,{img}"   # gpt-image-2 takes the bytes inline

edited = mai.run_model("openai/gpt-image-2", {
    "input_images": [data_uri],
    "prompt": "<the scene/edit>. Keep the person and everything else exactly the same.",
    "number_of_images": 1,
})
video = mai.run_model("kwaivgi/kling-v2.1", {
    "start_image": edited,
    "prompt": "<lively motion of the scene>",
    "duration": 5,
    "mode": "standard",
})
print(video)
```

Then send the video with `send_video` outside the script.

## Models that worked (empirically)

- Edit while preserving the likeness: `openai/gpt-image-2` (best), `qwen/qwen-image-edit-plus` (good alt).
- Image → video: `kwaivgi/kling-v2.1`; if the motion looks poor, try `bytedance/seedance-1-pro`.
- Unsure of a model's exact inputs? Call `mai.get_model_schema("<model>")` **before** `run_model`
  and pass exactly its fields — don't guess parameter names.

## Tips

- "just make a video from the photo" → skip the edit, animate the photo directly.
- Keep edit prompts explicit about preserving the face: "Do not change facial features, gender, or age."
- Keep your reply to the user in their language; this playbook is for you, not for them.

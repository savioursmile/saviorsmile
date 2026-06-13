# savioursmile — Portfolio + Discord Bot

A personal portfolio site with a Discord bot for live content management.

---

## Architecture

```
Discord Bot (Replit)
        ↕  slash commands
Flask API (Render)  ←→  Portfolio site (Render / GitHub Pages)
        ↓
Discord Webhook  ←  contact form submissions
        ↓
Email reply via /reply command
```

---

## Repositories / files

| File | Where it runs | Purpose |
|---|---|---|
| `index.html` | Render / GitHub Pages | Portfolio frontend |
| `api.py` | Render (Python web service) | REST API — stores projects & donate links |
| `bot.py` | Replit | Discord bot — slash commands |
| `requirements-api.txt` | Render | Flask + gunicorn deps |
| `requirements-bot.txt` | Replit | discord.py + aiohttp deps |

---

## Setup

### 1 — Deploy the API on Render

1. Create a new **Web Service** on [render.com](https://render.com)
2. Connect this repo, set **Root directory** to `.` and **Start command** to:
   ```
   gunicorn api:app
   ```
3. Set these environment variables in Render → Environment:

   | Key | Value |
   |---|---|
   | `API_SECRET` | Any strong random string |
   | `WEBHOOK_URL` | Your Discord channel webhook URL |

4. Copy your live Render URL, e.g. `https://jordan-api.onrender.com`

---

### 2 — Configure the portfolio

In `index.html`, find this line near the bottom and update it:

```js
const API_URL = 'https://YOUR-RENDER-APP.onrender.com';
```

The contact form now POSTs to your API instead of directly to Discord.  
Discord still receives the embed — plus each submission gets an ID you can reply to with `/reply`.

---

### 3 — Deploy the bot on Replit

1. Create a new **Python** Repl and upload `bot.py` + `requirements-bot.txt`
2. Add these **Secrets** (padlock icon in Replit):

   | Key | Value |
   |---|---|
   | `DISCORD_TOKEN` | Your bot token from [discord.com/developers](https://discord.com/developers/applications) |
   | `API_URL` | Your Render API URL |
   | `API_SECRET` | Same secret as above |
   | `SMTP_HOST` | e.g. `smtp.gmail.com` |
   | `SMTP_PORT` | `587` |
   | `SMTP_USER` | Your email address |
   | `SMTP_PASS` | Your email password or app password |
   | `FROM_EMAIL` | Sender address shown to recipients (can be same as `SMTP_USER`) |

3. Run with:
   ```
   python bot.py
   ```

> **Gmail tip:** Use an [App Password](https://myaccount.google.com/apppasswords) instead of your real password. Enable 2FA first.

---

## Bot commands

| Command | Description |
|---|---|
| `/project list` | List all projects with their IDs |
| `/project add title: … description: … tags: React,Node span: 4` | Add a project card live |
| `/project edit proj-abc field: title value: New name` | Edit any field of a project |
| `/project remove proj-abc` | Delete a project card |
| `/donate list` | Show current donation links |
| `/donate set name: Ko-fi url: https://… label: One-time support` | Add or update a donation link |
| `/donate remove Ko-fi` | Remove a donation link |
| `/reply to: user@email.com subject: Re: your message message: Hi!` | Email a reply to a contact form submission (ephemeral) |
| `/status` | API health check + project/donate counts |

---

## API endpoints

All `POST`, `PATCH`, `DELETE` routes require the header `X-Secret: <API_SECRET>`.  
`GET` routes and `/contact` are public.

| Method | Path | Body |
|---|---|---|
| `GET` | `/projects` | — |
| `POST` | `/projects` | `{ title, description, tags[], icon, span }` |
| `PATCH` | `/projects/<id>` | Any subset of the above |
| `DELETE` | `/projects/<id>` | — |
| `GET` | `/donate` | — |
| `POST` | `/donate` | `{ name, url, label, icon }` |
| `DELETE` | `/donate/<name>` | — |
| `POST` | `/contact` | `{ name, email, message }` — called by the portfolio form |
| `GET` | `/submissions` | List all contact form submissions (secret required) |

---

## License

Each project is licensed individually. Only a person who has paid for a project may use it. Others may use it only with explicit permission from the owner, or if the owner has marked that project as public.

To request permission or purchase a license, use the contact form on the site.

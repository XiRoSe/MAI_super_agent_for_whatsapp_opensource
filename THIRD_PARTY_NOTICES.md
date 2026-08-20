# Third-Party Notices

MAI (MIT, © 2026 XiRoSe) depends on the third-party components below. Each is
licensed by its respective owner under the license indicated; those licenses
apply to those components, not to MAI's own code. This list covers the direct,
notable dependencies — the lockfiles (`pyproject.toml`,
`frontend/package-lock.json`) are authoritative for the full tree.

## Companion service (separate repo, wired by docker-compose)

| Component | License | Notes |
|---|---|---|
| [mai-gowa](https://github.com/XiRoSe/mai-gowa) — fork of [GOWA (go-whatsapp-web-multidevice)](https://github.com/aldinokemal/go-whatsapp-web-multidevice) | MIT | Upstream copyright and license retained in the fork. |
| [whatsmeow](https://github.com/tulir/whatsmeow) (via the gateway) | MPL-2.0 | WhatsApp Web multi-device protocol library. Not modified by this project. |

## Backend (Python)

| Component | License |
|---|---|
| [Agno](https://github.com/agno-agi/agno) (agent framework / AgentOS) | Apache-2.0 |
| [FastAPI](https://github.com/fastapi/fastapi) | MIT |
| [Uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause |
| [SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy) | MIT |
| [psycopg 3](https://github.com/psycopg/psycopg) | LGPL-3.0 |
| [Pydantic](https://github.com/pydantic/pydantic) / pydantic-settings | MIT |
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause |
| [cryptography](https://github.com/pyca/cryptography) | Apache-2.0 / BSD-3-Clause (dual) |
| [PyNaCl](https://github.com/pyca/pynacl) | Apache-2.0 |
| [croniter](https://github.com/kiorky/croniter) | MIT |
| [PyJWT](https://github.com/jpadilla/pyjwt) | MIT |
| [bcrypt](https://github.com/pyca/bcrypt) | Apache-2.0 |
| [replicate](https://github.com/replicate/replicate-python) | Apache-2.0 |
| [e2b-code-interpreter](https://github.com/e2b-dev/code-interpreter) | Apache-2.0 |
| [ddgs](https://github.com/deedy5/ddgs) (DuckDuckGo search) | MIT |
| [NumPy](https://github.com/numpy/numpy) | BSD-3-Clause |

## Frontend (JavaScript/TypeScript)

| Component | License |
|---|---|
| [React](https://github.com/facebook/react) / react-dom | MIT |
| [Ant Design](https://github.com/ant-design/ant-design) (antd + @ant-design/icons) | MIT |
| [react-router-dom](https://github.com/remix-run/react-router) | MIT |
| [axios](https://github.com/axios/axios) | MIT |
| [dayjs](https://github.com/iamkun/dayjs) | MIT |
| [Vite](https://github.com/vitejs/vite) / TypeScript | MIT / Apache-2.0 |

## Fonts

The dashboard references the **Clash Display** and **Satoshi** typefaces,
© Indian Type Foundry, loaded at runtime from the
[Fontshare](https://www.fontshare.com/) CDN under the
[ITF Free Font License (FFL)](https://www.fontshare.com/licenses/itf-ffl).
The font files are **not included in this repository**; if you fork and
redistribute a build that bundles them, review the FFL terms yourself.

## Trademark notice

WhatsApp is a trademark of WhatsApp LLC / Meta Platforms, Inc. MAI is not
affiliated with them — see [DISCLAIMER.md](DISCLAIMER.md).

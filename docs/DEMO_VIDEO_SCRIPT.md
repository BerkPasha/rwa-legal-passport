# Demo video script (3 minutes)

The video uses only what is publicly reachable and working today:
- the website;
- the read-only interactive demo;
- the two real Stellar Testnet transactions, opened on a public explorer.

Every value shown is synthetic demo data.

**Before recording**
- Open https://legalpassport.vercel.app/ in a clean browser window at 1440 px or wider, with no other tabs, bookmarks bar or extensions visible.
- Keep a second tab ready on the Stellar Expert page of the current transaction: https://stellar.expert/explorer/testnet/tx/843a5a6d9101b3e93a591c88008cf81eca9f25d0f2bd803d5741ef6c3c1240da
- Narration is about 400 words: speak at a calm pace. Timings are targets.

| Time | Screen | Narration |
|---|---|---|
| 0:00–0:15 | Website hero | "A tokenized asset is issued once, but its legal documents keep changing. Today, nobody can tell which document version a claim came from, who approved it, or whether that approval still applies. RWA Legal Passport gives every tokenized asset one reviewable legal record." |
| 0:15–0:40 | Hero diagram "One asset · one record": move the pointer down the five stages, then across to the dashed column | "One asset, five stages. The asset. Its legal documents, each version hashed with SHA-256. Legal claims, each quoting an exact page. Human approvals, bound to the version they reviewed. And cryptographic verification on Stellar Testnet. On the right is what version 2 superseded: version 1, its claims, its approvals and its anchor. They are kept as history but no longer count." |
| 0:40–1:00 | Click **Launch App** → the demo **Overview** | "This is the interactive demo. It is read-only and has no sign-in. Three synthetic assets: one current, one that needs reapproval after a document change, one awaiting review." |
| 1:00–1:30 | **Follow one asset → See what changed**: the LPDEMO **What Changed** tab | "LPDEMO's prospectus was approved at version 1. Then version 2 was uploaded. The version 1 approvals stopped applying automatically. Nobody had to remember to revoke them. Version 2 was then reviewed and approved again." |
| 1:30–2:00 | Back, then **Read the claims and their quotations**: the claims review with the page text | "Every claim cites a page and an exact quotation, for example Income Rights on page 2. The server checks the quotation against the stored text of that page of that version. A claim that cannot be found there cannot be approved. Here are the two approvals: the issuer's attestation and the independent legal reviewer's." |
| 2:00–2:20 | **Open the Legal Passport**: point at the request box, then the **Passport History** tab | "This is what an institution reads through a scoped, read-only API key: only the currently approved claims, the approvals behind them, and the full version history." |
| 2:20–2:45 | **Stellar Evidence** tab; open the transaction on Stellar Expert in the second tab | "The passport's evidence manifest is hashed, and the digest is committed on Stellar Testnet as the transaction's memo hash. This is the real transaction on the public network, at ledger 5,285,093. Anyone can check it independently." |
| 2:45–3:00 | Back to the website; the line under the diagram | "An anchor proves that this exact evidence digest was recorded at that ledger time and that the record is unchanged since. It does not prove legal validity. Approvals stay human. RWA Legal Passport: the legal evidence layer for real-world assets." |

**What not to say or show**
- Do not call the demo's verification live. In the demo it is a check recorded on 19 Oct 2026. The transaction itself is live on Testnet and can be opened on the explorer.
- Do not claim AI extraction: it is optional, disabled by default and not verified live.
- Do not claim Mainnet, hosting of the backend, or legal validity.
- Do not show the local `.env`, `access_control.json`, `secrets/` or any terminal with tokens.

**Notes**
- On the explorer, point at the transaction hash, the ledger and the memo type (hash), which match the demo's Stellar Evidence tab. Explorers may encode the memo differently from the demo's hex digest: Horizon, for example, returns it base64-encoded. So do not compare the memo text on screen.
- Stellar Testnet is reset periodically. Open the explorer link the day you record; if it no longer resolves, show the demo's Stellar Evidence tab only and say that the transaction was on Testnet before its reset.

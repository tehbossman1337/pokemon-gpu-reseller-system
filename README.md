# Inventory Agent

Inventory, order tracking from iCloud email, and Valor accounts/profiles (import, edit, rejig, export).

## Install on Windows 11 (64-bit) - no Python needed
1. Make a free GitHub account, create a **private** repository, upload the contents of this folder
   (include the hidden `.github` folder).
2. Open the repo's **Actions** tab > **Build Windows installer** > **Run workflow**.
3. When it turns green (about 5 minutes), open the run and download **ValorInventoryAgent-Setup**
   (a zip with `ValorInventoryAgent-Setup.exe` inside).
4. Run the setup. It installs for your user (no admin needed), adds Start-menu and desktop shortcuts, and can be
   removed from Settings > Apps. Windows may show a blue "protected your PC" screen: More info > Run anyway
   (the installer isn't code-signed).

Your data is kept in `%APPDATA%\ValorInventoryAgent` and survives updates and uninstall. Back it up.
Exports are saved to `Documents\Inventory Agent Exports`.

## Using it
- **Accounts / Profiles tabs:** Import Valor file (your Valor exports load unchanged, every field is kept),
  edit any field, tick rows for bulk actions, Rejig for address variants, "Change address for many" to move
  all or ticked profiles to a new address, Export Valor file to import back into Valor.
- **Settings:** iCloud email + app-specific password (account.apple.com > Sign-In and Security) for order sync.

Passwords and card numbers are stored unencrypted on your PC, so use BitLocker and don't share that folder.

## Updates
Open the **Updates** tab: Check for updates, Download update (with progress), Install & restart. The installer
overwrites the program files and reopens the app; your data is kept. If your GitHub repo is private, paste a
read-only GitHub token on that tab.

## Email
Settings > "Load my folders" lets you pick a dedicated folder (e.g. Pokemon Center). Every email in a folder other
than INBOX is read. The **Inbox** tab shows your mail read-only, with what the scraper makes of each email.

## Reselling tracker (v8+)
The app now includes the full Pokémon / Graphics Card reselling tracker: Dashboard, Inventory, Sales (automatic platform fees, profit, ROI), Receipts, Scan Station, Tax Summary, settings for fees, currencies and alerts, and CSV import/export. Delivered orders found by the email scraper are added to Inventory automatically (weighted-average cost).

## Updates
Every push to `main` builds a new installer and publishes it as a GitHub release. The app checks on start, downloads automatically (Settings → "Download app updates automatically"), and shows an "Install & restart" bar. If the repository is private, paste a read-only GitHub token under Updates (or make the repo public).

## Tests
`python selftest.py` runs the backend checks (the build runs it before packaging). `python gen_embedded.py` rebuilds the embedded UI after editing `static/index.html`.

## Email scanning
By default the app reads **every folder, all time** (Trash and Drafts skipped) and every message, so order emails are found even in forwards, other folders or odd senders. Messages already read are remembered and skipped. Settings → Email settings lets you pick folders or limit it to likely order emails. When the email reader improves, everything is re-read once automatically (safe: no double stock).

## Privacy blur
Valor Accounts/Profiles show full shipping and billing addresses. Turn on **Blur** (header button or the bar above the tables) to blur addresses, card details, email/phone and passwords/proxies; click any value to reveal it. The choice is remembered.

## Package tracker & notifications
- **Packages** (Orders group): when a store email says an order shipped, the tracking number is read from it, linked to the order and followed until delivered. Carrier emails (UPS/USPS/FedEx/DHL/OnTrac/LaserShip/Amazon) and store "delivered" emails update it automatically; you can also add numbers by hand.
- **Live tracking (optional):** paste a free 17TRACK key in Settings → Package tracking to poll carriers every N minutes, even when no email arrives.
- **Alerts** (header button): in-app notification list with unread badge. Optional pushes to Windows desktop pop-ups, Discord, Slack or ntfy (phone), with quiet hours and per-event toggles. "Send a test" reports each channel's result.

## Accounts & Profiles layout
Site rail with counts, stats strip, search/sort, Cards or Table view with paging, credit-card visuals, ship-to/bill-to blocks, selection bar for bulk actions. The Blur option still covers addresses, cards, emails and passwords.

## Scan Station & phone pairing
Scans match your own SKU labels or a product's printed barcode (stored on the item, so repeat scans are instant). USB/Bluetooth scanners work as-is. "Turn on phone scanning" opens a scan-only page on your Wi-Fi (QR code + secret link); scanner apps that open a URL or type like a keyboard can feed the same box.

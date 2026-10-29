# Valor Inventory Agent

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
Exports are saved to `Documents\Valor Inventory Agent Exports`.

## Using it
- **Accounts / Profiles tabs:** Import Valor file (your Valor exports load unchanged, every field is kept),
  edit any field, tick rows for bulk actions, Rejig for address variants, "Change address for many" to move
  all or ticked profiles to a new address, Export Valor file to import back into Valor.
- **Settings:** iCloud email + app-specific password (account.apple.com > Sign-In and Security) for order sync.

Passwords and card numbers are stored unencrypted on your PC, so use BitLocker and don't share that folder.

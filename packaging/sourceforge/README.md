# SourceForge mirror

Everything needed to put UniFi Stock Watcher on SourceForge: the project's settings, the listing text to paste,
and how releases get there. GitHub stays the source of truth. SourceForge mirrors its release files; the code,
the issues and the in-app updater all stay on GitHub.

## 1. Create the project

Go to <https://sourceforge.net/p/add_project> (sign in or register first; a new account may be asked for phone
verification).

| Field | Enter |
|---|---|
| Project Name | `UniFi Stock Watcher` |
| URL name | `unifi-stock-watcher` (3-30 letters, digits and dashes; it can't be changed later without a support ticket) |
| Tools | **Files** and **Reviews** only. Leave out Code, Tickets, Discussion and Wiki: those live on GitHub, and a second copy would drift. |

The project will be at `https://sourceforge.net/projects/unifi-stock-watcher/`.

Don't use the GitHub *Importer* to create it. It makes a one-time copy of the code, issues and wiki, and that copy
then goes stale. Step 3 below brings in the releases only, and keeps them coming.

## 2. Fill in the listing

**Admin → Metadata** (on the project page, click the lock icon to unlock the menu, then **Admin**):

| Field | Enter |
|---|---|
| Name | `UniFi Stock Watcher` |
| Home Page | `https://jamesccupps.github.io/UnifiStockWatcher/` |
| Support Page | `https://github.com/jamesccupps/UnifiStockWatcher/issues` |
| Project Logo | `logo.png` from this folder (128 x 128) |
| Video | leave empty |

**Short Summary** (70 characters):

```
Alerts you the moment a product is back in stock on the Ubiquiti Store
```

**Full Description:**

```
UniFi Stock Watcher tells you the moment something you want is back in stock on the Ubiquiti Store (store.ui.com). Pick the products from the live catalog; the app checks the whole store on a timer and alerts you with a notification you can click, a sound, and the product's page in your browser.

Each check reads the entire UniFi catalog, about 490 products in 8 requests, whether you watch one product or fifty, and a live feed shows everything that came into or went out of stock anywhere in the store. Out-of-stock products show the store's own restock estimate and how long they've been sold out.

It runs in the notification area and can start with Windows, keeps a searchable history of changes, and updates itself with signed releases. Light and dark themes follow Windows. US, EU, UK and Canada stores. Also a command-line watcher for servers and Task Scheduler.

No account, no API key: it reads the store's public catalog at a polite interval. Free and open source (MIT). Source, issues and documentation: https://github.com/jamesccupps/UnifiStockWatcher

An independent project, not made, endorsed or supported by Ubiquiti. UniFi is a trademark of Ubiquiti Inc.
```

**Features** (Admin → Features, if the project shows that page; one per line):

```
Checks the whole UniFi catalog on every check, not one product at a time
Notifications you can click to open the product, plus a sound and the product page
Restock estimates and how long a product has been sold out
Live feed of every stock change across the store
History of changes, searchable and exportable as CSV
Runs in the notification area; Start with Windows
Signed one-click in-app updates
Windows 11-style light and dark themes in any accent colour
US, EU, UK and Canada stores
Installer (no admin) or portable zip; command-line watcher included
```

**Categories** (Admin → Categorization; pick the nearest SourceForge offers):

| Category | Choose |
|---|---|
| License | MIT License |
| Operating System | Windows (and Windows 10 / Windows 11 where listed) |
| Programming Language | Python |
| User Interface | Tk; Win32 (MS Windows) |
| Intended Audience | End Users/Desktop; System Administrators |
| Topic | Monitoring; Internet |
| Development Status | 5 - Production/Stable |
| Translations | English |

**Screenshots** (Admin → Screenshots). Upload from `site/images/`, with these captions:

| File | Caption |
|---|---|
| `watch-list-dark.png` | The watch list: favourites first, a product back in stock, restock estimates, and the store-wide changes feed |
| `add-products-dark.png` | Add products: the live catalog, searchable, by category, with stock status |
| `watch-list-light.png` | The light theme |
| `history-light.png` | History: every stock change, for your products or the whole store |
| `settings-dark.png` | Settings take effect as you change them |

They come from the app's demo mode (real product names, made-up stock, prices and dates), never a real watch list.

## 3. Bring in the releases

On the project page: click the lock icon to unlock the menu, then the gear icon next to **Files** → **GitHub
Integration**. Enter `jamesccupps/UnifiStockWatcher` and approve the connection.

From then on, every new GitHub release's files are imported automatically shortly after it's published.

- **3.0.0 was published before the integration existed**, so it isn't imported by itself. SourceForge
  re-checks a release when its text is edited, so edit the v3.0.0 release's notes on GitHub (for example, add a
  line `Also on SourceForge: https://sourceforge.net/projects/unifi-stock-watcher/`) and save.
- SourceForge doesn't notice files added to a release after it was published. The release workflow attaches
  every file when it creates the release, so that's fine as long as releases aren't hand-edited to add files.

## 4. The default download

Once 3.0.0's files are in **Files**, hover over `UnifiStockWatcher-Setup.exe`, click its info (**i**) button,
and tick it as the default download for **Windows**. The big green button on the project page then offers the
installer. It keeps offering the file you chose, so choose the new installer after each release.

## 5. Afterwards

- The download counts take a few hours to start.
- Point people at GitHub for everything but the files: the Support Page above does that; if Reviews ask
  questions, answer with a link to the issues.
- Add the SourceForge link to the README's download line and the site footer once the project is live.

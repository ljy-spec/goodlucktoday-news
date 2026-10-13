# Good Luck Today public news

This repository contains only a standalone public-news collector, its GitHub Actions workflow, and public headline title/link snapshots on the news-data branch. The private app repository, user notes, tasks, calendars, billing data, credentials and Cloudflare tokens are not included.

Scheduled collection runs at minutes 7, 27 and 47 using standard public GitHub-hosted Ubuntu runners. Scheduling can be delayed; snapshots expire one hour after original collection. Sources are credited inside each edition. The collector validates fixed source URLs, languages, body limits and safe headline links.

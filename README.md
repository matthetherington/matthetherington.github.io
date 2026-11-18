# Matt Hetherington's notes

A small Jekyll blog using the [Chirpy theme](https://github.com/cotes2020/jekyll-theme-chirpy),
published at the canonical address:

**https://blog.hetherington.tv/**

The default GitHub Pages address, `matthetherington.github.io`, redirects here.

The homepage lists posts; the sidebar includes categories, tags, archives,
gists and an about page. Self-contained downloads live under `/downloads/`.
Chirpy supplies light/dark mode, search, a table of contents and copyable code
blocks. New visitors start in dark mode; the sidebar selector can switch to
Light, Dark or System and remembers the choice. Existing Light/Dark preferences
are respected.

The gem supplies prebuilt theme JavaScript, so Node is not needed for building
or previewing. Node 18+ is used only for the theme-behaviour tests in CI.

There is no `/blog` prefix, analytics tracker or comment service.
PWA/offline caching is disabled so it cannot serve a stale downloadable script.
Theme libraries use Chirpy's standard external CDNs. The sidebar headshot and
favicons are hosted locally. Chirpy's default CC-BY notice is suppressed for original blog
prose; the downloadable script retains its explicit GPL license.

## Publish on GitHub Pages

The site uses a custom **GitHub Actions** build so its Jekyll dependencies can be
kept current rather than using GitHub Pages' older built-in Jekyll bundle.

1. In **Settings → Pages → Build and deployment**, change **Source** to
   **GitHub Actions**.
2. In **Settings → Pages**, set **Custom domain** to `blog.hetherington.tv`.
   At your DNS provider, point its CNAME record to `matthetherington.github.io`.
   This Actions deployment does not need a `CNAME` file. Enable **Enforce HTTPS**
   once GitHub has provisioned the certificate.
3. Commit and push the site changes to the existing **`master`** branch.
4. Check the **Build and deploy blog** workflow in **Actions**.

Pull requests build and validate without deploying. Pushes to `master` and
manual workflow runs on `master` deploy only after the checks pass. Editing files
locally does not publish anything.

## Write a post

Create `_posts/YYYY-MM-DD-a-short-title.md`:

```markdown
---
title: "What I worked on"
description: "A short summary."
---

Explain the problem and the solution.

## Requirements

Give prerequisites and installation commands before the script link.

## Get the script

[View the gist](https://gist.github.com/matthetherington/YOUR_GIST_ID)

## Usage

Give copy-and-paste commands and their expected output.
```

Posts automatically get a layout, `/posts/a-short-title/` URL, homepage entry,
Atom feed entry and sitemap entry. Add `categories: [Linux, Fedora Asahi]` and
`tags: [asahi, trackpad]` in front matter to populate Chirpy's category/tag pages.
Don't use a future date unless you intend the post to remain
unpublished for now.

For an unpublished work in progress, use `_drafts/`; `_drafts/new-post.md` is a
template and is not deployed as a post. Preview drafts with
`bundle exec jekyll serve --drafts`.

## Preview and validate locally

Use Ruby **3.4** (see `.ruby-version`), Bundler, and Python 3:

```bash
bundle install
bundle exec jekyll serve --host 127.0.0.1
```

Visit `http://127.0.0.1:4000`. The canonical links deliberately use the
production custom domain, even during local previews.

Run the same production checks used in CI:

```bash
JEKYLL_ENV=production bundle exec jekyll build --strict_front_matter --trace
python3 scripts/check-site.py
python3 -m unittest discover -s tests -v
node --test tests/test-theme.cjs
bundle exec htmlproofer _site --disable-external
```

The checks cover canonical URLs, navigation, search, feed/sitemap, the downloadable
script's checksum, the post's prerequisite/download order, shell syntax, and the
exclusion of drafts/development files from the published site. They do **not**
execute the post's install, download or driver-build commands.

If you don't have Ruby installed, use rootless Podman instead:

```bash
podman run --rm -it \
  --publish 127.0.0.1:4000:4000 \
  --security-opt label=disable \
  --volume "$PWD:/work" --workdir /work \
  --env BUNDLE_PATH=/work/vendor/bundle \
  docker.io/library/ruby:3.4 \
  sh -ec 'bundle install && bundle exec jekyll serve --host 0.0.0.0'
```

The published port is bound to localhost only. This installs gems in ignored
`vendor/bundle`, not into your host's Ruby installation. Stop it with Ctrl+C.

Chirpy supplies its own Atom feed; don't add `jekyll-feed`, which would generate
a second file at the same URL. The small `_plugins/posts-lastmod-hook.rb` uses
Git history for updated dates, so the workflow checks out full history.

Keep `theme_mode` unset: Chirpy treats `theme_mode: dark` as a forced mode and
hides the selector. `_includes/metadata-hook.html` instead uses Chirpy's theme API
to set Dark once for a new visitor. A separate initialization marker preserves an
explicit System choice, for which Chirpy intentionally clears its own saved key.

To refresh dependencies intentionally, run `bundle update`, then build and
validate before committing the updated `Gemfile.lock`.

## The trackpad script

`downloads/build-magicmouse-palm.py` is a standalone driver builder. The post
links to this site's copy, not to a Gist that does not exist yet.
`downloads/LICENSE` contains its GPL license.

When updating the script, update its SHA-256 in `_data/downloads.yml`:

```bash
sha256sum downloads/build-magicmouse-palm.py
```

The post renders the checksum from that data file. CI compares it with the actual
published download, so a stale or mistyped checksum fails the build.

The default build and `--check` leave the system unchanged. The separate
root-only `--install BUILD_DIR` action enables a service that switches the
internal trackpad on the matching kernel at boot. `--uninstall` removes the
service and module; reboot to restore the stock driver.

See `NOTICE.md` for the Chirpy Starter code attribution.

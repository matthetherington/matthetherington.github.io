# Track the most recent author date after a post has more than one commit.
# Adapted from Chirpy Starter's posts-lastmod-hook (MIT), using argv rather than
# interpolating a post path into a shell command.
require "open3"

Jekyll::Hooks.register :posts, :post_init do |post|
  output, status = Open3.capture2(
    "git", "log", "-2", "--format=%aI", "--", post.relative_path,
    chdir: post.site.source
  )
  dates = output.lines.map(&:strip).reject(&:empty?)
  post.data["last_modified_at"] ||= dates.first if status.success? && dates.length > 1
end

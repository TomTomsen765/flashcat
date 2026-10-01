# Homebrew formula for Flashcat. The published copy lives in the tap repository TomTomsen765/homebrew-tap
# (Formula/flashcat.rb); update url and sha256 there for every release:
#   curl --proto '=https' --tlsv1.2 -fsSL https://github.com/TomTomsen765/flashcat/archive/refs/tags/vX.Y.Z.tar.gz | shasum -a 256
class Flashcat < Formula
  desc "Local AI assistant for the macOS terminal (LM Studio or Ollama)"
  homepage "https://tomtomsen765.github.io/flashcat/"
  url "https://github.com/TomTomsen765/flashcat/archive/refs/tags/v1.3.12.tar.gz"
  sha256 "REPLACE_WITH_SHA256_OF_THE_RELEASE_TARBALL"
  license "MIT"

  depends_on :macos

  def install
    libexec.install "bin/flashcat", "bin/flashcat-chat.py", "bin/flashcat-cleanup"
    bin.install_symlink libexec/"flashcat"
  end

  def caveats
    <<~EOS
      Flashcat needs a local model server: LM Studio (https://lmstudio.ai/download) or Ollama.
      Open it once, then start Flashcat in a project folder:
        cd ~/Documents/my-project && flashcat
      The first start offers to download the default model, Gemma 4 26B (about 16 GB).
    EOS
  end

  test do
    assert_match "Flashcat #{version}", shell_output("#{bin}/flashcat --version")
  end
end

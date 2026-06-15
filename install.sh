#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status
set -e

# ANSI Color Codes
GREEN='\033[0;32m'
TEAL='\033[0;36m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Display ASCII banner
echo -ne "${TEAL}${BOLD}"
cat << 'EOF'
  ____                               _ _ _ 
 / ___|_ __ _   _ _ __   ___ ___  __| (_) | ___ 
| |   | '__| | | | '_ \ / __/ _ \/ _` | | |/ _ \
| |___| |  | |_| | |_) | (_| (_) | (_| | | |  __/
 \____|_|   \__, | .__/ \___\___/\__,_|_|_|\___|
            |___/|_|                            
EOF
echo -ne "${NC}"

echo -e "${TEAL}=================================================${NC}"
echo -e "${TEAL}      Crypcodile CLI Framework Installer         ${NC}"
echo -e "${TEAL}=================================================${NC}"
echo

# 1. Verify python3 is installed and checks that the version is >= 3.12
echo -e "Verifying Python 3 version..."

PYTHON_CMD=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

check_version() {
    local cmd="$1"
    "$cmd" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >/dev/null 2>&1
}

PYTHON_OK=false
if [ -n "$PYTHON_CMD" ]; then
    if check_version "$PYTHON_CMD"; then
        PYTHON_OK=true
    fi
fi

if [ "$PYTHON_OK" = false ]; then
    echo -e "${RED}Error: Python 3.12+ was not found on your system.${NC}"
    if [ -n "$PYTHON_CMD" ]; then
        CURRENT_VER=$("$PYTHON_CMD" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
        echo -e "Found Python version: ${YELLOW}${CURRENT_VER}${NC} (Minimum required: 3.12)"
    else
        echo -e "Python 3 is not installed."
    fi
    echo
    echo -e "${BOLD}How to install Python 3.12+ on your system:${NC}"
    echo -e "----------------------------------------"
    echo -e "${BOLD}macOS (using Homebrew):${NC}"
    echo -e "  brew install python@3.12"
    echo
    echo -e "${BOLD}Ubuntu/Debian:${NC}"
    echo -e "  sudo apt update"
    echo -e "  sudo apt install -y python3.12 python3.12-venv python3-pip"
    echo
    echo -e "${BOLD}Fedora/CentOS/RHEL:${NC}"
    echo -e "  sudo dnf install python3.12"
    echo
    echo -e "${BOLD}Arch Linux:${NC}"
    echo -e "  sudo pacman -S python"
    echo -e "----------------------------------------"
    exit 1
fi

CURRENT_VER=$("$PYTHON_CMD" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
echo -e "Python version verified: ${GREEN}${CURRENT_VER}${NC}"

# Check for git
if ! command -v git >/dev/null 2>&1; then
    echo -e "${RED}Error: 'git' is not installed, which is required to download the package from GitHub.${NC}"
    echo -e "Please install git first. For example:"
    echo -e "  macOS:  brew install git"
    echo -e "  Ubuntu: sudo apt install git"
    exit 1
fi
echo -e "Git version verified: ${GREEN}$(git --version | head -n 1)${NC}"

# 2. Creates the directory ~/.crypcodile
echo -e "Creating directory ~/.crypcodile..."
mkdir -p "$HOME/.crypcodile"

# 3. Creates a virtual environment ~/.crypcodile/venv
echo -e "Creating virtual environment at ~/.crypcodile/venv..."
if ! "$PYTHON_CMD" -m venv "$HOME/.crypcodile/venv"; then
    echo -e "${RED}Error: Failed to create virtual environment.${NC}"
    echo -e "This is often because the python3-venv package is not installed."
    echo -e "If you are on Ubuntu/Debian, please run:"
    echo -e "  sudo apt install -y python3-venv"
    exit 1
fi

# 4. Upgrades pip and installs the CLI package
echo -e "Upgrading pip inside virtual environment..."
"$HOME/.crypcodile/venv/bin/pip" install --upgrade pip

echo -e "Installing Crypcodile from Git repository..."
if ! "$HOME/.crypcodile/venv/bin/pip" install "git+https://github.com/nazmiefearmutcu/Crypcodile.git"; then
    echo -e "${RED}Error: Package installation failed.${NC}"
    exit 1
fi

# 5. Configures a wrapper script at ~/.local/bin/crypcodile
echo -e "Configuring wrapper script at ~/.local/bin/crypcodile..."
mkdir -p "$HOME/.local/bin"
cat << 'EOF' > "$HOME/.local/bin/crypcodile"
#!/bin/sh
exec "$HOME/.crypcodile/venv/bin/crypcodile" "$@"
EOF
chmod +x "$HOME/.local/bin/crypcodile"

# 6. Safely updates ~/.bashrc and ~/.zshrc
echo -e "Updating shell PATH configuration..."
UPDATED_PROFILES=()

for profile in "$HOME/.bashrc" "$HOME/.zshrc"; do
    # Even if they don't exist, we can touch them
    touch "$profile"
    if ! grep -q '\.local/bin' "$profile"; then
        echo -e "\n# Crypcodile CLI PATH configuration" >> "$profile"
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$profile"
        UPDATED_PROFILES+=("$profile")
    fi
done

# 7. Displays prominent notification message
echo
echo -e "${GREEN}${BOLD}========================================================================${NC}"
echo -e "${GREEN}${BOLD}Crypcodile has successfully downloaded!${NC}"
echo -e "${BOLD}You must restart your terminal or run:${NC}"
for profile in "${UPDATED_PROFILES[@]}"; do
    echo -e "  source ~/${profile##*/}"
done
if [ ${#UPDATED_PROFILES[@]} -eq 0 ]; then
    echo -e "  source ~/.zshrc  # (or ~/.bashrc depending on your shell)"
fi
echo -e "${BOLD}before using it.${NC}"
echo -e "${GREEN}${BOLD}========================================================================${NC}"
echo

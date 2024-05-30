#!/bin/bash
# Install Polite Lib v0.0.2
# Pull and install the Polite-Lib python library.
# This is mostly setup for Docker installations.
set -e
INSTALL_DIR=""
if [ -z "$LIB_DIR" ]; then
    mkdir -p ${LIB_DIR}
    cd ${LIB_DIR}
    INSTALL_DIR="${LIB_DIR}/polite-lib"
else
    INSTALL_DIR="./polite-lib"
fi

if [ -z "$POLITE_LIB_BRANCH" ]; then
	POLITE_LIB_BRANCH="main"
fi

echo "Building Polite-Lib in ${INSTALL_DIR} on branch ${POLITE_LIB_BRANCH}"

if [ ! -d ${INSTALL_DIR} ]; then
  git clone https://github.com/politeauthority/polite-lib.git
fi

cd polite-lib/src/
git checkout ${POLITE_LIB_BRANCH}
git pull origin ${POLITE_LIB_BRANCH}
pip3 install -r requirements.txt
python3 setup.py build
python3 setup.py install
echo "Installed successfully"

#!/bin/bash
# Install Polite Lib v0.0.4
# Pull and install the Polite-Lib python library.
# This is mostly setup for Docker installations.
set -e
INSTALL_DIR=""
if [ -z "$LIB_DIR" ]; then
    cd ${LIB_DIR}
    INSTALL_DIR="${LIB_DIR}/polite-lib"
else
    INSTALL_DIR="/polite-lib"
fi

if [ -z "$POLITE_LIB_BRANCH" ]; then
	POLITE_LIB_BRANCH="main"
fi

if [ ! -z "$INSTALL_DIR" ]; then
	mkdir -p ${INSTALL_DIR}
fi
echo "Building Polite-Lib in ${INSTALL_DIR} on branch ${POLITE_LIB_BRANCH}"

# Install through wget on GitHub
cd ${INSTALL_DIR}
wget https://github.com/politeauthority/polite-lib/archive/refs/heads/${POLITE_LIB_BRANCH}.zip
mv ${POLITE_LIB_BRANCH}.zip polite-lib-${POLITE_LIB_BRANCH}.zip
unzip polite-lib-${POLITE_LIB_BRANCH}.zip
cd polite-lib-${POLITE_LIB_BRANCH}/src/
pip install -r requirements.txt
python3 setup.py build
python3 setup.py install
echo "Polite-Lib installed successfully"

#!/usr/bin/env bash
# AECProbe 手动构建 (无 Gradle): aapt2 link -> javac -> d8 -> zip -> zipalign -> apksigner
set -e
export ANDROID_HOME="$HOME/Library/Android/sdk"
BT="$ANDROID_HOME/build-tools/34.0.0"
ANDROID_JAR="$ANDROID_HOME/platforms/android-34/android.jar"
PKG=com.aecprobe
PROJ=/Users/alamn/aecprobe
cd "$PROJ"

echo "=== 0. 工具检查 ==="
ls "$BT"/{aapt2,d8,zipalign,apksigner} "$ANDROID_JAR" >/dev/null
java -version 2>&1 | head -1

echo "=== 1. aapt2 link (manifest -> base.apk) ==="
rm -rf build && mkdir -p build/cls build/dex
"$BT/aapt2" link -o build/base.apk -I "$ANDROID_JAR" \
  --manifest AndroidManifest.xml --min-sdk-version 29 --target-sdk-version 34

echo "=== 2. javac ==="
javac -source 1.8 -target 1.8 -classpath "$ANDROID_JAR" -d build/cls src/com/aecprobe/MainActivity.java 2>&1 | grep -v "warning:" || true
ls build/cls/com/aecprobe/

echo "=== 3. d8 -> classes.dex ==="
"$BT/d8" --output build/dex --lib "$ANDROID_JAR" --min-api 29 build/cls/com/aecprobe/*.class 2>&1 | tail -3
ls build/dex/

echo "=== 4. 合 dex 进 apk ==="
cp build/base.apk build/app-unsigned.apk
( cd build/dex && zip -j -q ../app-unsigned.apk classes.dex )

echo "=== 5. zipalign ==="
"$BT/zipalign" -p -f 4 build/app-unsigned.apk build/app-aligned.apk

echo "=== 6. keystore (如无) + apksigner ==="
[ -f build/debug.keystore ] || keytool -genkeypair -alias androiddebugkey -keyalg RSA -keysize 2048 \
  -validity 10000 -keystore build/debug.keystore -storepass android -keypass android \
  -dname "CN=AECProbe,O=AECProbe,C=CN" 2>/dev/null
"$BT/apksigner" sign --ks build/debug.keystore --ks-pass pass:android --key-pass pass:android \
  --out build/AECProbe.apk build/app-aligned.apk
"$BT/apksigner" verify --print-certs build/AECProbe.apk 2>&1 | head -2

echo "=== DONE ==="
ls -la build/AECProbe.apk

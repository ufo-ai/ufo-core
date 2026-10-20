DISABLE_COMMAND = (
    "set -eu; "
    "find /usr/lib -type f \\( -name EUC-JISX0213.so -o -name SHIFT_JISX0213.so \\) -delete; "
    "find /usr/lib -type f \\( -name gconv-modules -o -name gconv-modules-extra.conf \\) "
    "-exec sed -i '/EUC-JISX0213\\|SHIFT_JISX0213/d' {} +; "
    "iconvconfig; "
    "for encoding in EUC-JISX0213 EUC_JISX0213 SHIFT_JISX0213 SHIFT-JISX0213; do "
    'if LC_ALL=C iconv -f "$encoding" -t UTF-8 </dev/null 2>/tmp/gconv-error; '
    "then exit 1; fi; "
    "grep -q 'is not supported' /tmp/gconv-error; done; "
    "rm /tmp/gconv-error; "
    "test \"$(printf '\\202\\240' | iconv -f SHIFT_JIS -t UTF-8)\" "
    "= \"$(printf '\\343\\201\\202')\"; "
    "test \"$(printf '\\244\\242' | iconv -f EUC-JP -t UTF-8)\" "
    "= \"$(printf '\\343\\201\\202')\"; "
    'test "$(printf x | iconv -f ISO-2022-JP-3 -t UTF-8)" = x; '
    'test "$(printf x | iconv -f UTF-8 -t UTF-8)" = x'
)

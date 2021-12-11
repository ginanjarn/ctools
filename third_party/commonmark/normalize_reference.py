"""Case-folding and whitespace normalization"""
# Unicode Case Folding table has been derived from the following work:
#
#   CaseFolding-12.0.0.txt
#   Date: 2019-01-22, 08:18:22 GMT
#   (c) 2019 Unicode(R) Inc.
#   Unicode and the Unicode Logo are registered trademarks
#   of Unicode, Inc. in the U.S. and other countries.
#   For terms of use, see http://www.unicode.org/terms_of_use.html
#
#   Unicode Character Database
#     For documentation, see http://www.unicode.org/reports/tr44/

import re
import sys
from builtins import str, chr

__all__ = ["normalize_reference"]

if sys.version_info < (3,) and sys.maxunicode <= 0xFFFF:
    # shim for Python 2.x UCS2 build
    _unichr = chr

    def chr(cdp):
        if 0x10000 <= cdp < 0x110000:
            cdp -= 0x10000
            return _unichr(0xD800 | (cdp >> 10)) + _unichr(0xDC00 | (cdp & 0x3FF))
        return _unichr(cdp)


def _parse_table(tbl):
    xlat = {}
    cur_i, cur_j = -1, 0
    for entry in tbl.split(";"):
        arr = entry.split(",")
        info = [int(x, 36) if x else 0 for x in arr[0].split(":")]
        arr = [int(x, 36) for x in arr[1:]]
        assert not any(x in xlat for x in arr)
        sfx = "".join(map(chr, arr))
        streak, stride = 0, 1
        if len(info) == 2:
            fdt, delta = info
        elif len(info) == 3:
            fdt, streak, delta = info
        else:
            fdt, streak, delta, stride = info
        assert streak >= 0 and stride >= 1
        cur_i += fdt + 1
        cur_j -= delta
        assert cur_j != 0
        i = cur_i
        last = cur_i + streak
        while i <= last:
            # uniqueness and idempotency
            assert i not in xlat and i + cur_j not in xlat
            assert i not in arr
            xlat[i] = chr(i + cur_j) + sfx
            i += stride
    return xlat


XLAT = _parse_table(
    # ===== Start of Unicode Case Folding table =====
    "1t:p:-w;37:-kn;a:m:kn;n:6:;6:3w,37;w:1a:-31:2;1b:5k,lj;1:4:-5k:2;6:e::"
    "2;f:-aa,32;:18:aa:2;19:3e;:4:-3e:2;5:7h;1:-da;:2:5t:2;3:-5p;:5p;1:1:-5"
    "o;1:5o;2:-26;:-3f;:-1;:5m;1:-5o;:-2;1:-4;:2;:5s;3:-5u;:-2;1:-1;:4:5x:2"
    ";5:-61;:61;1:-61;2:61;1:-61;:61;1:1:-60;1:2:60:2;3:-62;:4:62:4;b:-1;:1"
    ";1:-1;:1;1:-1;:g:1:2;i:g::2;h:av,lo;:-aw;:2:1:2;3:2q;:-15;:12:-1l:2;13"
    ":3n;1:g:-3n:2;n:-8bu;:8bu;1:4k;:-8gb;2:8br;1:5g;:-7c;:-2;:8:1y:2;72:-3"
    "7;16:2:37:2;5:;8:-37;6:26;1:2:1;3:-r;1:1:1;1:m,lk,ld;:g:9;h:8:;c:b,lk,"
    "ld;h:k;c:-7;:12;:-5;3:-a;:7;1:m:-n:2;n:1j;:-6;2:c;:4;1:-1t;1:8;:-8;2:2"
    ":3n;2:f:-5u;f:v:1c;27:w:v:2;15:1g::2;1h:-e;:c:e:2;e:2m::2;2o:11:-1b;2d"
    ":2a,136;26w:11:-5mq;12:6::6;mo:5:5m0;1on:4sm;:-1;:-9;:1:-2;1:1;:-7;:-o"
    ";:-vzb;7:16:tj7;18:2:;8y:44:-2bl:2;45:5yn,mp;:-b,lk;:-2,lm;:-1,lm;:p,j"
    "i;:-5xb;2:5wx,37;1:2m:-5yk:2;2v:7:9;f:5:;f:7:;f:7:;f:5:;7:5fn,lv;1:2,l"
    "v,lc;1:2,lv,ld;1:2,lv,n6;2:6:-5ft:2;e:7:;n:7:3c,qh;7:7:8,qh;7:7:-o,qh;"
    "7:7:8,qh;7:7:-1k,qh;7:7:8,qh;9:-6,qh;:5hc,qh;:6,qh;1:-3,n6;:1,n6,qh;:1"
    ":-5j2;1:1:1u;1:5hd,qh;1:-6;3:-5h3,qh;:5ha,qh;:a,qh;1:-7,n6;:1,n6,qh;:3"
    ":-5h6;3:5hb,qh;5:4,lk,lc;:1,lk,ld;2:3,n6;:1,lk,n6;:1:-5jq;1:1:2k;7:5h5"
    ",lk,lc;:1,lk,ld;:5,lv;1:-2,n6;:1,lk,n6;:1:-5ju;1:1:2w;1:-2x;5:33,qh;:5"
    "h0,qh;:-4,qh;1:7,n6;:1,n6,qh;:1:-5gu;1:1:-2;1:5h1,qh;89:8a;3:o2;:-3d;6"
    ":-6ea;19:f:c;y:f;mq:p:-p;1ft:1a:-m;2n:1b;1:8ag;:-5ch;:5c1;2:4:-8a0:2;5"
    ":8bh;:-v;:y;:-1;1:3:-8bj:3;b:1:8cg;1:2q:-8cg:2;2y:2::2;6:nym::nym;nyn:"
    "16::2;1p:q::2;4h:c::2;f:1o::2;1y:2::2;3:r9h;:8:-r9h:2;c:;1:wmh;2:2:-wm"
    "h:2;5:i::2;j:wn9;:b;:-4;:-a;:3;1:-1e;:o;:-l;:-xbp;:a:pr:2;d:;1:1d;:wlv"
    ";:-5cb;q1:27:2oo;fpr:jii,2u;:1,2x;:1,30;:1,2u,2x;:1,2u,30;:-c,38;:1,38"
    ";c:-z8,12u;:1,12d;:1,12j;:-9,12u;:b,12l;sp:p:-1cjn;ym:13:-8;4v:z:;1jj:"
    "1e:-o;2e7:v:w;gwv:v:;o8v:x:-2"
    # ===== End of Unicode Case Folding table =====
)


def _check_native(tbl):
    """
    Determine if Python's own native implementation
    subsumes the supplied case folding table
    """
    try:
        for i in tbl:
            stv = chr(i)
            if stv.casefold() == stv:
                return False
    except AttributeError:
        return False
    return True


# Hoist version check out of function for performance
SPACE_RE = re.compile(r"[ \t\r\n]+")
if _check_native(XLAT):

    def normalize_reference(string):
        """
        Normalize reference label: collapse internal whitespace
        to single space, remove leading/trailing whitespace, case fold.
        """
        return SPACE_RE.sub(" ", string[1:-1].strip()).casefold()


elif sys.version_info >= (3,) or sys.maxunicode > 0xFFFF:

    def normalize_reference(string):
        """
        Normalize reference label: collapse internal whitespace
        to single space, remove leading/trailing whitespace, case fold.
        """
        return SPACE_RE.sub(" ", string[1:-1].strip()).translate(XLAT)


else:

    def _get_smp_regex():
        xls = sorted(x - 0x10000 for x in XLAT if x >= 0x10000)
        xls.append(-1)
        fmt, (dsh, opn, pip, cse) = str("\\u%04x"), str("-[|]")
        rga, srk, erk = [str(r"[ \t\r\n]+")], 0, -2
        for k in xls:
            new_hir = (erk ^ k) >> 10 != 0
            if new_hir or erk + 1 != k:
                if erk >= 0 and srk != erk:
                    if srk + 1 != erk:
                        rga.append(dsh)
                    rga.append(fmt % (0xDC00 + (erk & 0x3FF)))
                if new_hir:
                    if erk >= 0:
                        rga.append(cse)
                    if k < 0:
                        break
                    rga.append(pip)
                    rga.append(fmt % (0xD800 + (k >> 10)))
                    rga.append(opn)
                srk = k
                rga.append(fmt % (0xDC00 + (srk & 0x3FF)))
            erk = k
        return re.compile(str().join(rga))

    def _subst_handler(matchobj):
        src = matchobj.group(0)
        hiv = ord(src[0])
        if hiv < 0xD800:
            return " "
        return XLAT[0x10000 + ((hiv & 0x3FF) << 10) | (ord(src[1]) & 0x3FF)]

    SMP_RE = _get_smp_regex()

    def normalize_reference(string):
        """
        Normalize reference label: collapse internal whitespace
        to single space, remove leading/trailing whitespace, case fold.
        """
        return SMP_RE.sub(_subst_handler, string[1:-1].strip()).translate(XLAT)

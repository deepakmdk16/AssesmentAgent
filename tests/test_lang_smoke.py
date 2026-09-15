"""Gate G3 (audit R2-007, R2-008, R2-092, R2-093, R2-094, R2-105): every language the
grader offers compiles and runs one program that uses the maths library, prints
non-ASCII, declares a package/module and uses one idiom its pinned toolchain
supports — through `run_submission`, the real grading path, and inside the built
image through the real jail.

Each program reads 144 from stdin and must print "12 café ✓". What each one proves
on top of that is in its comment; a wrong toolchain flag is a compile error or a
wrong answer here, not a candidate's 0%.

On a dev box a toolchain may be missing or older than the idiom needs, and the
program SKIPs. That is a green lie where the toolchains are promised, so
scripts/lang-smoke.sh runs this file inside the production image with
ASSESS_REQUIRE_TOOLCHAINS=1, which turns every skip into a failure and adds the
pin diff (test_the_image_matches_the_pin) — the posture of ASSESS_REQUIRE_NSJAIL.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

from assessment_agent import toolchains
from assessment_agent.languages import LANGUAGES
from assessment_agent.questions import TestCase
from assessment_agent.runner import run_submission

_REQUIRED = os.environ.get("ASSESS_REQUIRE_TOOLCHAINS") == "1"

STDIN = "144\n"
EXPECTED = "12 café ✓"


@dataclass(frozen=True)
class Smoke:
    source: str
    needs: tuple[int, ...]  # the lowest toolchain version the idiom compiles on


SMOKE: dict[str, Smoke] = {
    # match statement (3.10); math module; PEP 538 makes the C locale UTF-8.
    "python": Smoke(
        "import math\nimport sys\n\n"
        "n = int(sys.stdin.read())\n"
        "match n:\n    case 0:\n        r = 0\n    case _:\n        r = int(math.sqrt(n))\n"
        'print(f"{r} café ✓")\n',
        needs=(3, 10),
    ),
    # Array.prototype.toSorted (Node 20).
    "javascript": Smoke(
        'const n = Number(require("fs").readFileSync(0, "utf8").trim());\n'
        "const [root] = [0, Math.sqrt(n)].toSorted((a, b) => b - a);\n"
        "console.log(`${Math.trunc(root)} café ✓`);\n",
        needs=(20, 0),
    ),
    # Hash#except (3.0); Integer.sqrt; UTF-8 source and output.
    "ruby": Smoke(
        "n = $stdin.read.to_i\n"
        'parts = { root: Integer.sqrt(n), tag: "café ✓", junk: 1 }.except(:junk)\n'
        'puts "#{parts[:root]} #{parts[:tag]}"\n',
        needs=(3, 0),
    ),
    # package main; slices + the max builtin (1.21). Compiled by a real compile step
    # (R2-092), so a syntax error here is a compile error, not N runtime errors.
    "go": Smoke(
        "package main\n\n"
        'import (\n\t"fmt"\n\t"math"\n\t"slices"\n)\n\n'
        "func main() {\n"
        "\tvar n float64\n"
        "\tif _, err := fmt.Scan(&n); err != nil {\n\t\tpanic(err)\n\t}\n"
        "\txs := []int{0, int(math.Sqrt(n))}\n"
        "\tslices.Sort(xs)\n"
        '\tfmt.Printf("%d café ✓\\n", max(xs[0], xs[1]))\n'
        "}\n",
        needs=(1, 21),
    ),
    # A package declaration (R2-094); record + var (16); the non-ASCII output is the
    # R2-093 proof — under file.encoding=ANSI_X3.4-1968 it prints "12 caf? ?".
    "java": Smoke(
        "package assess.smoke;\n\n"
        "import java.util.Scanner;\n\n"
        "public class Main {\n"
        "    record Answer(int root, String tag) {}\n\n"
        "    public static void main(String[] args) {\n"
        "        var in = new Scanner(System.in);\n"
        '        var a = new Answer((int) Math.sqrt(in.nextDouble()), "café ✓");\n'
        '        System.out.println(a.root() + " " + a.tag());\n'
        "    }\n"
        "}\n",
        needs=(16, 0),
    ),
    # pow needs -lm (R2-007); __OPTIMIZE__ is defined only at -O1 and up (R2-008);
    # _Static_assert is C11.
    "c": Smoke(
        "#include <math.h>\n#include <stdio.h>\n\n"
        "#ifndef __OPTIMIZE__\n#error \"built without optimisation\"\n#endif\n"
        '_Static_assert(sizeof(int) >= 4, "int");\n\n'
        "int main(void) {\n"
        "    double n;\n"
        "    if (scanf(\"%lf\", &n) != 1) return 1;\n"
        '    printf("%d café ✓\\n", (int)pow(n, 0.5));\n'
        "    return 0;\n"
        "}\n",
        needs=(4, 6),
    ),
    # <cmath>, __OPTIMIZE__ (R2-008), structured bindings and std::optional (C++17).
    "cpp": Smoke(
        "#include <cmath>\n#include <iostream>\n#include <optional>\n#include <string>\n\n"
        "#ifndef __OPTIMIZE__\n#error \"built without optimisation\"\n#endif\n\n"
        "static std::optional<double> read() {\n"
        "    double n;\n"
        "    if (std::cin >> n) return n;\n"
        "    return std::nullopt;\n"
        "}\n\n"
        "int main() {\n"
        "    auto n = read();\n"
        "    if (!n) return 1;\n"
        '    auto [root, tag] = std::pair{static_cast<int>(std::sqrt(*n)), std::string("café ✓")};\n'
        '    std::cout << root << " " << tag << "\\n";\n'
        "}\n",
        needs=(11, 0),
    ),
    # let-else (1.65) and is_some_and (1.70); a module; debug_assert! only fires in a
    # debug build, so it panics unless rustc ran with -O (R2-008).
    "rust": Smoke(
        "use std::io::Read;\n\n"
        "mod tag {\n"
        '    pub fn text() -> &\'static str {\n        "café ✓"\n    }\n'
        "}\n\n"
        "fn main() {\n"
        '    debug_assert!(false, "debug build: rustc must run with -O");\n'
        "    let mut s = String::new();\n"
        "    std::io::stdin().read_to_string(&mut s).unwrap();\n"
        "    let Ok(n) = s.trim().parse::<f64>() else {\n"
        "        std::process::exit(1)\n"
        "    };\n"
        "    if !Some(n).is_some_and(|v| v >= 0.0) {\n"
        "        std::process::exit(1)\n"
        "    }\n"
        '    println!("{} {}", n.sqrt() as i64, tag::text());\n'
        "}\n",
        needs=(1, 70),
    ),
}


def test_every_language_has_a_smoke_program():
    assert set(SMOKE) == set(LANGUAGES)


def test_the_pin_is_at_least_what_each_program_needs():
    # The image is built to the pin, so this is what makes the programs runnable
    # there; it also stops a pin from quietly dropping below an idiom in use.
    for language, smoke in SMOKE.items():
        pinned = toolchains.version_tuple(toolchains.pinned()[language])
        assert pinned >= smoke.needs, f"{language}: pinned {pinned} < needs {smoke.needs}"


def _skip_or_fail(reason: str) -> None:
    if _REQUIRED:
        pytest.fail(f"ASSESS_REQUIRE_TOOLCHAINS=1: {reason}")
    pytest.skip(reason)


@pytest.mark.parametrize("language", sorted(LANGUAGES))
def test_each_language_compiles_and_runs_the_smoke_program(language):
    live = toolchains.probe_one(language)
    have = toolchains.version_tuple(live)
    smoke = SMOKE[language]
    if not have:
        _skip_or_fail(f"{language}: toolchain not usable ({live})")
    if have < smoke.needs:
        _skip_or_fail(f"{language}: {live} is older than the idiom needs {smoke.needs}")
    report = run_submission(
        smoke.source, language, (TestCase("smoke", STDIN, EXPECTED),), time_limit_s=15.0
    )
    assert report.infra_error is None, report.infra_error
    assert report.compile_error is None, report.compile_error
    outcome = report.outcomes[0]
    assert report.all_passed, outcome.error or outcome.actual


@pytest.mark.skipif(not _REQUIRED, reason="gcc-only behaviour; runs inside the image")
def test_c_still_compiles_a_submission_that_forgets_an_include():
    # Image-only: this asserts what GNU gcc 14 does with -fpermissive, and a dev box
    # may have clang, which errors on an implicit declaration whatever the flag says.
    src = (
        "#include <stdio.h>\n"
        "int main(void) {\n"
        "    char b[8];\n"
        '    strcpy(b, "12");\n'  # no <string.h>: an error on gcc 14 without the flag
        '    printf("%s café ✓\\n", b);\n'
        "    return 0;\n"
        "}\n"
    )
    report = run_submission(
        src, "c", (TestCase("include", STDIN, EXPECTED),), time_limit_s=15.0
    )
    assert report.compile_error is None, report.compile_error
    assert report.all_passed, report.outcomes[0].error or report.outcomes[0].actual


@pytest.mark.skipif(not _REQUIRED, reason="pin diff runs inside the built image only")
def test_the_image_matches_the_pin():
    # R2-105: the versions a candidate is told about are the ones that grade them.
    # A base-image or apt bump that moves a toolchain fails here until the pin —
    # and with it what the platform displays — is updated in the same commit.
    lines = toolchains.drift(toolchains.probe(), toolchains.pinned())
    assert lines == [], "\n".join(lines)

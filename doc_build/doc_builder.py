#! /usr/bin/env python3
import argparse
import inspect
import shutil
import subprocess
import sys
import os
import re
import time
from pathlib import Path
from datetime import datetime
from typing import Dict

try:
    import yaml
except ImportError:
    sys.exit("Please install the PyYAML package: pip install PyYAML.")

if sys.version_info < (3, 10):
    sys.exit("Python 3.10 or greater is required.")


if sys.platform == "win32":
    from subprocess import list2cmdline as _to_shell_cmd_base
else:
    from shlex import join as _to_shell_cmd_base

def to_shell_cmd(command):
    return _to_shell_cmd_base([str(x) for x in command])


class Logger:

    def __log(self, msg, *args, **kwargs):
        print(msg, *args, **kwargs)
        return self

    def __call__(self, msg, *args, **kwargs):
        return self.__log(msg, *args, **kwargs)

    def __lshift__(self, msg):
        return self.__log(msg)


log = Logger()


class ExecCommand:
    def __init__(self, binary_name):
        if binary := shutil.which(binary_name):
            self.binary = binary
        else:
            sys.exit(f"Please install {binary_name}")

    def __run(self, arguments, stderr_processor=None, *args, **kwargs):
        command = [self.binary] + arguments
        print(f"Running command: {to_shell_cmd(command)}")
        if stderr_processor:

            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                *args, **kwargs
            )
            std_out, std_err = process.communicate()

            if std_err := std_err.decode("utf-8"):
                stderr_processor(std_err)

            if std_out := std_out.decode("utf-8"):
                log(std_out)
        else:
            subprocess.check_call(command, *args, **kwargs)

        return self

    def __call__(self, arguments, *args, **kwargs):
        return self.__run(arguments, *args, **kwargs)

    def __lshift__(self, arguments):
        return self.__run(arguments)

    def get_output(self, arguments, *args, **kwargs):
        command = [self.binary] + arguments
        print(f"Running command: {to_shell_cmd(command)}")

        return subprocess.check_output(command, *args, **kwargs).decode("utf-8")


pandoc = ExecCommand("pandoc")
git = ExecCommand("git")


class DocBuilder:
    def __init__(self):
        super().__init__()

    # MARK: Target Functions
    def build_docs(self, args):
        log(f"Building documentation in {args.output}...")
        if args.clean:
            self.clean_docs(args)

        args.output.mkdir(parents=True, exist_ok=True)

        shutil.copytree(
            self.get_specification_root(),
            self.get_artifacts_dir(args.output),
            dirs_exist_ok=True,
        )
        combined = self.preprocess_build(args)

        spec = self.get_metadata_defaults_file()
        subtitle = self.get_subtitle(spec)

        fontpath = (Path(__file__).resolve().parent / "front_page").as_posix() + "/"
        dejavufontpath = (Path(__file__).resolve().parent / "fonts").as_posix() + "/"

        doc_build_filters = []
        for doc_filter in self.get_doc_build_filters():
            doc_build_filters.extend(["-F", doc_filter])

        # Set the cwd to the artifacts dir because it's easier for some filters to work relatively to it
        os.chdir(self.get_artifacts_dir(args.output))
        shared_command = [
            "--defaults",
            spec,
            combined,
            "--from=gfm+",
            *doc_build_filters,
            "-V",
            f"date={datetime.today().strftime('%Y-%m-%d')}",
            "-V",
            f"fontpath={fontpath}",
            "-V",
            f"dejavufontpath={dejavufontpath}",
            "-V",
            f"subtitle={subtitle}",
            "-V",
            "geometry:margin=1in",
            # "geometry:margin=1cm",
            # "-V", "geometry:top=1cm", "-V", "geometry:bottom=2cm", "-V", "geometry:left=1cm", "-V", "geometry:right=1cm",
            "-V",
            # "linestretch=1.0",
            "linestretch=1.25",
            "-V",
            "fontsize=10pt",
            # "-V",
            # "mainfont=DejaVu Serif",
            # "-V",
            # "monofont=DejaVu Sans Mono",
            # "-V",
            # "monofontoptions=Scale=0.8",  # scale down a bit for better sizing of listings and PEG
            "-V",
            f"AOUSD_ARTIFACTS_ROOT={self.get_artifacts_dir(args.output)}",
            "-V", "colorlinks=true",
            "-V", "linkcolor=OliveGreen",
            "-V", "toccolor=OliveGreen",
            "-V", "citecolor=OliveGreen",
            "-V", "urlcolor=blue",
            "--toc=true",
            "--toc-depth",
            "2",
            "--standalone",
            "--number-sections=true",
            "--from=markdown-hard_line_breaks",
            "--pdf-engine=tectonic",
        ]

        if not args.no_draft:
            log("\tAdding Draft Watermark...")
            shared_command.extend(["-V", "draft=true"])

        pdf = None
        docx = None
        html = None

        filename = self.get_file_base_name()

        if not args.no_html:
            html = args.output / f"{filename}.html"
            html_template = self.get_scripts_root() / "template" / "default.html5"
            log(f"\tBuilding HTML to {html}...")
            pandoc(
                shared_command
                + [
                    "-o",
                    html,
                    "--toc",
                    "--standalone",
                    "--mathml",
                    "--embed-resources",
                    f"--template={html_template}",
                ]
            )

        if not args.no_pdf:
            pdf = args.output / f"{filename}.pdf"
            latex_template = self.get_scripts_root() / "template" / "default.latex"
            log(f"\tBuilding PDF to {pdf}...")

            def stderr_processor(std_err):
                lines = std_err.splitlines()

                for line in lines:
                    # Spurious warning: https://github.com/tectonic-typesetting/tectonic/discussions/1192#discussioncomment-9463365
                    if line.startswith(
                        "warning: Trying to include PDF file with version "
                    ):
                        continue
                    # Can be safely ignored: https://www.overleaf.com/learn/how-to/Understanding_underfull_and_overfull_box_warnings
                    if line.startswith("warning: texput.") and (
                        "Overfull " in line or "Underfull " in line
                    ):
                        continue
                    # Can also be safely ignored
                    if line.startswith("warning: accessing absolute path "):
                        continue

                    # Just reporting fluff
                    if line.startswith("warning: warnings were issued"):
                        continue

                    log(line, file=sys.stderr)

            pandoc(
                shared_command + ["-o", pdf, f"--template={latex_template}"],
                stderr_processor=stderr_processor,
            )

        if not args.no_docx:
            docx = args.output / f"{filename}.docx"
            log(f"\tBuilding DocX to {docx}...")
            pandoc(shared_command + ["-o", docx, "-F", self.get_filter("convert_svg")])

        if args.pandoc_ast_json:
            log(f"\tBuilding Pandoc AST JSON to {args.output}...")
            pandoc(shared_command + ["-t", "json", "-o", args.output / "pandoc_ast.json"])

        return pdf, docx, html

    def get_doc_build_filters(self):
        """Return a list of paths to the filters the build_doc method runs in the order they must run"""
        return [
            self.get_filter("convert_mathblocks"),
            self.get_filter("header6"),
            self.get_filter("resolve_sections"),
            self.get_filter("sections_new_page"),
            self.get_filter("smaller_listings"),
        ]

    def get_file_base_name(self):
        tokens = ["aousd"]
        results = git.get_output(["remote", "-v"]).splitlines()
        for result in results:
            result = result.split("/")[-1].split()[0].replace(".git", "")
            tokens.extend([d for d in result.split("-") if d != "wg"])
            break
        filename = "_".join(tokens)
        return filename

    def preprocess_build(self, args, substitutions=None):
        artifacts = self.get_artifacts_dir(args.output)
        log(f"\tBuilding Preprocessing artifacts in {artifacts}...")

        entry_point = self.get_entry_point(args)
        combined = self.get_combined_file_name(args.output)

        self.flatten(args, entry_point, combined, substitutions=substitutions)

        if args.no_draft:
            self.add_publish_copyright(combined)
        else:
            self.add_draft_copyright(combined)

        return combined

    def flatten(self, args, source, output, substitutions: Dict[str, str] = None):
        log(f"\tFlattening {source}...")
        substitutions = substitutions or {}
        artifacts = self.get_artifacts_dir(args.output)
        with open(source, "r") as source_file:
            lines = source_file.readlines()
            with open(output, "w", encoding="utf-8") as out:
                for line in lines:
                    if res := re.search(r"\[(.*)]\((.*\.md)\)", line):
                        path = res.group(2)
                        tokens = path.split("/")
                        if len(tokens) > 1:
                            document = tokens[-2]
                        else:
                            document = os.path.splitext(tokens[-1])[0]
                        if not self.should_process(document, args):
                            continue

                        substituted_path = substitutions.get(path)
                        if substituted_path and os.path.exists(substituted_path):
                            path = substituted_path
                        else:
                            rel_path = os.path.join(os.path.dirname(source), path)
                            if os.path.exists(rel_path):
                                path = rel_path
                            else:
                                artifacts_path = os.path.join(artifacts, path)
                                if os.path.exists(artifacts_path):
                                    path = artifacts_path
                                else:
                                    raise IOError(f"Could not find {path}")
                        assert os.path.exists(path), f"Could not find {path}"

                        with open(path, "r", encoding="utf-8") as section:
                            out.write(section.read())
                            out.write("\n\n")

                    else:
                        out.write(line)

    def clean_docs(self, args):
        if args.output.exists():
            shutil.rmtree(args.output)

    def run_linter(self, args):
        combined = self.get_combined_file_name(args.output)
        log(f"Linting {combined} ...")
        assert combined.exists(), f"Could not find {combined}"

        linted = args.output / "linted.md"
        pandoc(
            ["-s", "-f", "markdown-smart", "--wrap=preserve", "-o", linted, combined]
        )

        log(f"\tLint output: {linted}")

    def export_git_archive(self, args):
        timestr = time.strftime("%Y%m%d-%H%M%S")
        filename = f"aousd_core_spec_{args.branch}_{timestr}.zip"
        filepath = args.output / filename
        log(f"Exporting archive to {filepath}...")
        git(["archive", "--format", "zip", "--output", filepath, args.branch])
        return filepath

    def display_todos(self, args):
        log(f"Listing Todos under {args.output}...")
        # Configuration for exclusions
        EXCLUDE_DIRS = {"docs", ".git", ".idea"}
        EXCLUDE_FILES = {"Makefile"}
        EXCLUDE_PATHS = {"./filters/pegen", "./trash", "./build", "./tools"}
        TODO_PATTERN = "TODO"
        FIXME_PATTERN = "FIXME"

        for dirpath, dirnames, filenames in os.walk(args.output):
            # Remove excluded directories from traversal
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

            for filename in filenames:
                filepath = Path(dirpath) / filename

                # Skip excluded files and paths
                if filename in EXCLUDE_FILES or any(
                    filepath.resolve().as_posix().startswith(ep) for ep in EXCLUDE_PATHS
                ):
                    continue

                # Check for 'TODO' and 'FIXME' in each file
                try:
                    with filepath.open("r", encoding="utf-8") as file:
                        for lineno, line in enumerate(file, start=1):
                            if (TODO_PATTERN in line) or (FIXME_PATTERN in line):
                                relative_path = os.path.relpath(filepath, ".")
                                log(f"{relative_path}:{lineno}:{line.strip()}")
                except (UnicodeDecodeError, OSError):
                    # Skip files that can't be read
                    pass

    def build_index(self, args):
        log(f"Building index from {args.output}...")
        artifacts = self.get_artifacts_dir(args.output)
        source = artifacts / "usd_spec.md"
        output_md = artifacts / "index.md"
        output_tsv = args.output / "index.tsv"

        index_yaml = artifacts / "build_index.yaml"
        self.write_yaml(index_yaml, {"OUTPUT": output_tsv.as_posix()})

        pandoc(
            [
                source,
                "--metadata-file",
                index_yaml,
                "-F",
                self.get_filter("generate_index"),
                "-o",
                output_md,
            ]
        )

        return output_tsv

    def display_spellcheck_issues(self, args):
        combined = self.get_combined_file_name(args.output)
        log(f"Checking spellings in {combined}...")
        assert combined.exists(), f"Could not find {combined}"

        fixed = args.output / "spellings_corrected.md"
        pandoc(["-F", self.get_filter("spellcheck"), combined, "-o", fixed])

        return fixed

    def display_style_issues(self, args):
        combined = self.get_combined_file_name(args.output)
        log(f"Checking styles in {combined}...")
        assert combined.exists(), f"Could not find {combined}"

        log(
            (
                "Legend:"
                "\n\t\033[91mWeasel Words\033[0m"
                "\n\t\033[95mIrregulars\033[0m"
                "\n\t\033[93mDuplicates\033[0m"
                "\n\n"
            )
        )

        # Define the default list of weasel words
        weasels = (
            "many|various|very|fairly|several|extremely|exceedingly|quite|remarkably|few|"
            "surprisingly|mostly|largely|huge|tiny|((are|is) a number)|excellent|"
            "interestingly|significantly|substantially|clearly|vast|relatively|completely"
        )
        weasel_pattern = re.compile(f"\\b({weasels})\\b", re.IGNORECASE)

        irregulars = (
            "awoken|been|born|beat|become|begun|bent|beset|bet|bid|bidden|bound|bitten|"
            "bled|blown|broken|bred|brought|broadcast|built|burnt|burst|bought|cast|"
            "caught|chosen|clung|come|cost|crept|cut|dealt|dug|dived|done|drawn|dreamt|"
            "driven|drunk|eaten|fallen|fed|felt|fought|found|fit|fled|flung|flown|"
            "forbidden|forgotten|foregone|forgiven|forsaken|frozen|gotten|given|gone|"
            "ground|grown|hung|heard|hidden|hit|held|hurt|kept|knelt|knit|known|laid|"
            "led|leapt|learnt|left|lent|let|lain|lighted|lost|made|meant|met|misspelt|"
            "mistaken|mown|overcome|overdone|overtaken|overthrown|paid|pled|proven|"
            "put|quit|read|rid|ridden|rung|risen|run|sawn|said|seen|sought|sold|sent|"
            "set|sewn|shaken|shaven|shorn|shed|shone|shod|shot|shown|shrunk|shut|sung|"
            "sunk|sat|slept|slain|slid|slung|slit|smitten|sown|spoken|sped|spent|spilt|"
            "spun|spit|split|spread|sprung|stood|stolen|stuck|stung|stunk|stridden|"
            "struck|strung|striven|sworn|swept|swollen|swum|swung|taken|taught|torn|"
            "told|thought|thrived|thrown|thrust|trodden|understood|upheld|upset|woken|"
            "worn|woven|wed|wept|wound|won|withheld|withstood|wrung|written"
        )
        irregular_pattern = re.compile(
            f"\\b(am|are|were|being|is|been|was|be)\\b[ ]*(\\w+ed|({irregulars}))\\b",
            re.IGNORECASE,
        )
        with open(combined, "r") as f:
            for lineno, line in enumerate(f.readlines(), start=1):
                if weasel_pattern.search(line):
                    highlighted_line = weasel_pattern.sub(
                        lambda m: f"\033[91m{m.group(0)}\033[0m", line
                    )
                    log(f"{lineno}: {highlighted_line.strip()}")
                if irregular_pattern.search(line):
                    highlighted_line = irregular_pattern.sub(
                        lambda m: f"\033[95m{m.group(0)}\033[0m", line
                    )
                    log(f"{lineno}: {highlighted_line.strip()}")

                last_word = ""
                words = re.split(r"(\W+)", line)

                for word in words:
                    # Skip spaces or empty strings
                    if not word.strip():
                        continue

                    # Skip punctuation
                    if re.match(r"^\W+$", word):
                        last_word = ""
                        continue

                    # Found a duplicate word?
                    if word.lower() == last_word.lower():
                        log(f"{lineno} \n\t\033[93m{word}\033[0m")

                    # Mark this as the last word
                    last_word = word

    # MARK: Path constants

    def _get_class_file(self) -> Path:
        return Path(inspect.getfile(self.__class__))

    def get_scripts_root(self) -> Path:
        return Path(__file__).resolve().parent

    def get_repo_root(self) -> Path:
        """Assumes that the repo root is two up from this root"""
        return self._get_class_file().parent.parent

    def get_specification_root(self) -> Path:
        return self.get_repo_root() / "specification"

    def get_default_build_output_root(self) -> Path:
        return Path(os.getenv("AOUSD_BUILD", self.get_repo_root() / "build"))

    def get_artifacts_dir(self, output_path: Path) -> Path:
        if not isinstance(output_path, Path):
            if hasattr(output_path, "output"):
                output_path = output_path.output
            else:
                raise TypeError("Output Path should be a Path object")
        return output_path / "artifacts"

    def get_entry_point(self, args) -> Path:
        return self.get_artifacts_dir(args.output) / "README.md"

    # MARK: Utility Functions

    def get_subtitle(self, defaults_file_path: Path):
        with open(defaults_file_path, "r") as f:
            spec_data = yaml.load(f, Loader=yaml.SafeLoader)
            commit = git.get_output(["rev-parse", "--short", "HEAD"], cwd=self.get_repo_root()).strip()
            subtitle = f"v{spec_data['metadata']['version']} ({commit})"
        return subtitle

    def get_combined_file_name(self, output_path: Path) -> Path:
        return self.get_artifacts_dir(output_path) / "usd_spec.md"

    def get_filter(self, name: str) -> Path:
        path = self.get_scripts_root() / "filters" / f"filter_{name}.py"
        assert path.exists(), f"Could not find {path}"
        return path

    def write_yaml(self, output: Path, data: dict):
        if isinstance(output, str):
            output = Path(str)
        with output.open("w") as f:
            yaml.dump(data, f)

    def should_process(self, document, args):
        if args.exclude and document in args.exclude:
            return False
        if args.only and document not in args.only:
            return False

        return True

    def get_metadata_defaults_file(self) -> Path:
        this_path = self._get_class_file()
        this_spec = this_path.parent / "defaults.yaml"

        if this_spec.exists():
            return this_spec

        return self.get_scripts_root() / "defaults.yaml"

    # MARK: Argparser builds

    def process_argparser(self):
        parser = argparse.ArgumentParser(description="Documentation Build Utilities")
        self.construct_subparsers(parser)

        parser.add_argument(
            "-o",
            "--output",
            help="Output directory",
            default=self.get_default_build_output_root(),
        )

        args = parser.parse_args()
        args.func(args)

    def construct_subparsers(self, parser):
        subparsers = parser.add_subparsers(dest="command", required=True)
        self.make_build_parser(subparsers)
        self.make_clean_parser(subparsers)
        self.make_lint_parser(subparsers)
        self.make_export_parser(subparsers)
        self.make_todo_parser(subparsers)
        self.make_index_parser(subparsers)
        self.make_spellcheck_parser(subparsers)
        self.make_style_parser(subparsers)
        return subparsers

    def make_build_parser(self, subparsers):
        build_parser = subparsers.add_parser("build", help="Build documentation")

        build_parser.set_defaults(func=self.build_docs)

        build_parser.add_argument(
            "--no-html", help="Do not build HTML", action="store_true"
        )
        build_parser.add_argument(
            "--no-pdf", help="Do not build PDF", action="store_true"
        )
        build_parser.add_argument(
            "--no-docx", help="Do not build docx", action="store_true"
        )
        build_parser.add_argument(
            "--pandoc-ast-json", help="Build Pandoc AST JSON output files (for diffing)", action="store_true"
        )
        build_parser.add_argument(
            "--clean", help="Clean before building", action="store_true"
        )
        build_parser.add_argument(
            "--only", help="Only build certain docs", nargs="*", default=[]
        )
        build_parser.add_argument(
            "--exclude", help="Exclude docs", nargs="*", default=[]
        )
        build_parser.add_argument(
            "--no-draft", help="Do not add draft watermark", action="store_true"
        )

        return build_parser

    def make_clean_parser(self, subparsers):
        clean_parser = subparsers.add_parser("clean", help="Clean documentation")
        clean_parser.set_defaults(func=self.clean_docs)
        return clean_parser

    def make_lint_parser(self, subparsers):
        lint_parser = subparsers.add_parser("lint", help="Lint documentation")
        lint_parser.set_defaults(func=self.run_linter)
        return lint_parser

    def make_export_parser(self, subparsers):
        export_parser = subparsers.add_parser("export", help="Export the git archive")
        export_parser.add_argument("-b", "--branch", default="main")
        export_parser.set_defaults(func=self.export_git_archive)
        return export_parser

    def make_todo_parser(self, subparsers):
        todo_parser = subparsers.add_parser(
            "todo", help="List all TODO and FIXME items"
        )
        todo_parser.set_defaults(func=self.display_todos)
        return todo_parser

    def make_index_parser(self, subparsers):
        index_parser = subparsers.add_parser("index", help="Build documentation index")
        index_parser.set_defaults(func=self.build_index)
        return index_parser

    def make_spellcheck_parser(self, subparsers):
        spellcheck_parser = subparsers.add_parser(
            "spellcheck", help="Spellcheck documentation"
        )
        spellcheck_parser.set_defaults(func=self.display_spellcheck_issues)
        return spellcheck_parser

    def make_style_parser(self, subparsers):
        style_parser = subparsers.add_parser(
            "stylecheck", help="Checks the style of the documentation"
        )
        style_parser.set_defaults(func=self.display_style_issues)
        return style_parser

    def add_publish_copyright(self, combined):
        intro_copyright = self.get_publish_intro_legalese()
        outro = self.get_publish_outro_legalese()
        content = self._read_file(combined)

        with open(combined, "w", encoding="utf-8") as f:
            f.write(intro_copyright)
            f.write(content)
            f.write(outro)

    def get_publish_intro_legalese(self):
        path = self.get_scripts_root() / "legal/publish_intro.md"
        return self._read_file(path)

    def get_publish_outro_legalese(self):
        path = self.get_scripts_root() / "legal/publish_outro.md"
        return self._read_file(path)

    def add_draft_copyright(self, combined):
        intro_copyright = self.get_intro_legalese()
        outro = self.get_outro_legalese()
        content = self._read_file(combined)

        with open(combined, "w", encoding="utf-8") as f:
            f.write(intro_copyright)
            f.write(content)
            f.write(outro)

    def get_intro_legalese(self):
        path = self.get_scripts_root() / "legal/draft_intro.md"
        return self._read_file(path)

    def get_outro_legalese(self):
        path = self.get_scripts_root() / "legal/draft_outro.md"
        return self._read_file(path)

    def _read_file(self, filename):
        if not filename.exists():
            raise IOError(f"Could not find {filename}")

        with open(filename, encoding="utf-8") as f:
            return f.read()


if __name__ == "__main__":
    DocBuilder().process_argparser()

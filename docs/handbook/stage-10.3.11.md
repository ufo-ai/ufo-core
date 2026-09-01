# Office XLSX LibreOffice Utilities  `stage-10.3.11`

This stage is behind-the-scenes support for working with Excel .xlsx files through LibreOffice, without showing the normal LibreOffice window. It is used when the system needs a spreadsheet to be updated and saved before another tool reads it.

The package marker file, __init__.py, is like a label on a toolbox. It tells Python that the scripts folder can be imported by other code, but it does not do any work itself. The _soffice.py helper is the shared adapter for LibreOffice. It supplies the common settings needed to run LibreOffice in “headless” mode, meaning in the background with no desktop interface. It also knows where LibreOffice keeps user macros on Linux and macOS.

The recalc.py script is the active worker. It opens a workbook in LibreOffice, tells it to recalculate all formulas, saves the updated file, and checks whether any spreadsheet errors remain. Together, these files let the system refresh spreadsheets reliably before later processing or validation.

## Files in this stage

### LibreOffice XLSX Script Utilities
Package setup and shared LibreOffice helpers support the recalculation script that refreshes workbook formulas and reports spreadsheet errors.

### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/__init__.py`

`other` · `import/package discovery`

This is an empty Python `__init__.py` file. Its job is not to perform work directly, but to give the surrounding folder a clear identity in Python’s import system. In plain terms, it is like putting a label on a drawer: the drawer may contain useful tools, and the label lets the rest of the program find them in an organized way.

Here, the folder belongs to an extension related to documents, specifically Office Excel `.xlsx` files. Any scripts placed in this directory can be treated as part of the same package. Without this file, depending on the Python version and packaging setup, imports or package discovery could become less predictable.

Because the file is empty, it does not create objects, read files, start processes, or change data. Its value is structural: it helps the codebase stay importable and organized.


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/_soffice.py`

`util` · `during document automation when a script needs LibreOffice`

Some spreadsheet-related scripts need LibreOffice to do work in the background, such as opening or converting Office files, without showing a graphical window. This file centralizes the few details needed to do that reliably. Think of it like a small adapter plug: other scripts do not need to remember the exact command name, environment setting, or macro folder path each time they talk to LibreOffice.

The key detail is the environment variable `SAL_USE_VCLPLUGIN`. An environment variable is a setting passed to a program when it starts. Here it is set to `svp`, which tells LibreOffice to use a headless or minimal display backend instead of trying to use the normal desktop interface. Without this, automated runs can fail on machines that do not have a visible desktop session.

The file also maps each supported operating system to the standard LibreOffice macro directory. Macros are small LibreOffice scripts stored in the user profile. Finally, it wraps `subprocess.run`, which is Python’s way of starting another program, so callers can run the `soffice` command and receive its captured output in a consistent way.

#### Function details

##### `soffice_env`  (lines 16–19)

```
def soffice_env() -> dict[str, str]
```

**Purpose**: Builds the environment settings used when starting LibreOffice in the background. Its main job is to add the setting that makes LibreOffice use a non-windowed display mode.

**Data flow**: It starts with a copy of the current process environment, adds or replaces `SAL_USE_VCLPLUGIN` with `svp`, and returns the updated dictionary. It does not change the process-wide environment; it prepares a separate set of settings for a LibreOffice child process.

**Call relations**: When `run_soffice` is about to start the `soffice` program, it calls `soffice_env` to get the safe background-running environment. The returned settings are then handed directly to `subprocess.run` so LibreOffice starts with those options.

*Call graph*: called by 1 (run_soffice).


##### `macro_dir`  (lines 22–25)

```
def macro_dir() -> Path
```

**Purpose**: Finds the folder where LibreOffice stores user macros for the current operating system. Scripts can use this when they need to install, read, or refer to LibreOffice macros.

**Data flow**: It checks the current operating system name, chooses the matching macro folder template for macOS or Linux, expands `~` into the user’s home folder, and returns the result as a `Path` object. If the system is not recognized, it falls back to the Linux-style location.

**Call relations**: This helper stands on its own for scripts that need the macro folder. It relies on the platform library to identify the operating system and on `Path` to return a path object that other file code can use easily.

*Call graph*: 2 external calls (Path, system).


##### `run_soffice`  (lines 28–34)

```
def run_soffice(args: list[str], timeout: int | None=None) -> subprocess.CompletedProcess[str]
```

**Purpose**: Runs the LibreOffice command-line program, `soffice`, with the given arguments and captures what it prints. Callers use it when they want LibreOffice to perform a task from an automation script.

**Data flow**: It receives a list of command-line arguments and an optional timeout. It builds a full command beginning with `soffice`, asks `soffice_env` for the proper background-running environment, then starts the program. It returns Python’s completed-process result, which includes the exit status plus captured standard output and error text.

**Call relations**: This is the main helper other scripts would call when they need LibreOffice to do work. Inside, it delegates environment preparation to `soffice_env`, then hands the command to `subprocess.run`, which actually launches LibreOffice and waits for it to finish.

*Call graph*: calls 1 internal fn (soffice_env); 1 external calls (run).


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/recalc.py`

`entrypoint` · `on-demand command execution`

Excel files often contain formulas whose saved results may be stale, especially if the file was changed by code rather than by Excel itself. This file solves that problem by using LibreOffice in headless mode, meaning LibreOffice runs in the background without showing a window. It installs a tiny LibreOffice macro if needed, then asks that macro to recalculate all formulas, save the workbook, and close it.

There is one extra wrinkle: LibreOffice can sometimes disturb table style information inside .xlsx files. An .xlsx file is really a zip package full of XML files, so the script first takes a small snapshot of table style XML, runs LibreOffice, and then patches those style snippets back in afterward. This is like taking a photo of a neatly set table before moving it, so you can put the tablecloth back the same way.

After recalculation, the script opens the workbook with openpyxl, a Python library for reading Excel files, and scans the calculated cell values for common Excel error strings such as #REF! and #DIV/0!. It returns a JSON-friendly summary with the number of formulas, the number of errors, and a short list of where errors were found.

#### Function details

##### `_ensure_macro`  (lines 43–62)

```
def _ensure_macro() -> bool
```

**Purpose**: Makes sure LibreOffice has the Basic macro needed to recalculate and save the workbook. Without this macro, the script could open LibreOffice but would not have a reliable command to force all spreadsheet formulas to update.

**Data flow**: It looks for the expected macro file in LibreOffice’s macro folder. If the file already contains the recalculation macro, it returns true. If the macro folder is missing, it briefly starts LibreOffice to create its user folders, then writes the macro file. It returns true if setup worked and false if writing the macro failed.

**Call relations**: The main recalc flow calls this before trying to process the spreadsheet. It relies on the shared LibreOffice helper functions to find the macro folder and supply the right environment, and it may call LibreOffice once just to initialize the macro directory.

*Call graph*: called by 1 (recalc); 3 external calls (macro_dir, soffice_env, run).


##### `_snapshot_table_styles`  (lines 65–73)

```
def _snapshot_table_styles(path: str) -> dict[str, bytes]
```

**Purpose**: Copies the table style snippets from inside the Excel file before LibreOffice touches it. This protects styling details that LibreOffice may otherwise rewrite or remove.

**Data flow**: It receives a path to an .xlsx file, opens it as a zip archive, and reads XML files under the workbook’s table folder. When it finds a table style element, it stores that raw XML bytestring in a dictionary keyed by the file name inside the archive. The result is a small backup of table style fragments.

**Call relations**: The recalc function calls this just before running LibreOffice. Its saved snippets are later passed to _restore_table_styles so the workbook can keep its original table style metadata.

*Call graph*: called by 1 (recalc); 1 external calls (ZipFile).


##### `_patch_table_style`  (lines 76–79)

```
def _patch_table_style(data: bytes, style_element: bytes) -> bytes
```

**Purpose**: Puts one saved table style XML snippet back into a table XML file. It is a small helper used while rebuilding the Excel zip package.

**Data flow**: It receives the current XML data for a table and the saved style element. If the table already has a table style element, it replaces it. If the style element is missing, it inserts the saved one just before the closing table tag. It returns the patched XML bytes.

**Call relations**: _restore_table_styles calls this for each table XML file whose style was backed up. It does not work with files directly; it only edits the XML content passed to it.

*Call graph*: called by 1 (_restore_table_styles).


##### `_restore_table_styles`  (lines 82–99)

```
def _restore_table_styles(path: str, styles: dict[str, bytes]) -> None
```

**Purpose**: Restores table style snippets that were saved before LibreOffice recalculated the workbook. This keeps the workbook’s visual table formatting metadata from being accidentally lost.

**Data flow**: It receives the workbook path and the dictionary of saved style snippets. If there are no snippets, it does nothing. Otherwise it creates a temporary zip copy of the workbook, reads every file from the original, patches matching table XML files, writes everything to the temporary file, and then replaces the original workbook with the patched copy. If something goes wrong, it removes the temporary file if it exists.

**Call relations**: The recalc function calls this after LibreOffice finishes successfully. It delegates the exact XML edit to _patch_table_style and uses zip and file-moving tools to rebuild the .xlsx package safely.

*Call graph*: calls 1 internal fn (_patch_table_style); called by 1 (recalc); 3 external calls (remove, move, ZipFile).


##### `_scan_errors`  (lines 102–114)

```
def _scan_errors(path: str) -> dict[str, list[str]]
```

**Purpose**: Looks through the recalculated workbook for visible Excel error values such as #REF!, #DIV/0!, and #N/A. This tells the caller whether recalculation succeeded cleanly or produced broken formulas.

**Data flow**: It opens the workbook with calculated values enabled, then visits every worksheet, row, and cell. If a cell’s value is text containing one of the known Excel error strings, it records the sheet name and cell address. It closes the workbook and returns a dictionary mapping each error type to the places where it appeared.

**Call relations**: The recalc function calls this after saving and restoring styles. Its results become the error summary returned to the command-line caller or any code using recalc directly.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `_count_formulas`  (lines 117–126)

```
def _count_formulas(path: str) -> int
```

**Purpose**: Counts how many formulas are present in the workbook. This gives useful context for the error report, such as whether a workbook had formulas to recalculate at all.

**Data flow**: It opens the workbook with formulas preserved rather than calculated values. It walks through every cell and counts text values that start with =, which is how spreadsheet formulas are written. It closes the workbook and returns the count.

**Call relations**: The recalc function calls this near the end, after checking for errors. The count is included in the final JSON-style result alongside the error totals.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `recalc`  (lines 129–181)

```
def recalc(filename: str, timeout: int=DEFAULT_TIMEOUT) -> dict
```

**Purpose**: Runs the full spreadsheet recalculation workflow for one Excel file and returns a structured result. This is the main reusable function for callers that want fresh formula values and a simple error report.

**Data flow**: It receives a filename and an optional timeout. It first checks that the file exists, prepares the LibreOffice macro, snapshots table styles, and then runs LibreOffice headlessly with the recalculation macro. If LibreOffice fails or times out, it returns an error dictionary. If recalculation succeeds, it restores saved table styles, scans for Excel error values, counts formulas, and returns a summary showing success or errors found.

**Call relations**: main calls this when the script is run from the command line. Inside, it coordinates the helper functions in order: macro setup, style snapshot, LibreOffice execution, style restoration, error scanning, and formula counting.

*Call graph*: calls 5 internal fn (_count_formulas, _ensure_macro, _restore_table_styles, _scan_errors, _snapshot_table_styles); called by 1 (main); 2 external calls (run_soffice, Path).


##### `main`  (lines 184–191)

```
def main() -> None
```

**Purpose**: Provides the command-line interface for the script. It lets a person or another process run recalculation by typing the file path and, optionally, a timeout.

**Data flow**: It reads command-line arguments from sys.argv. If no file is provided, it prints a usage message and exits with an error code. Otherwise it parses the filename and optional timeout, calls recalc, converts the returned dictionary to formatted JSON text, and prints it.

**Call relations**: This is the entry point when the file is executed directly. It hands all real spreadsheet work to recalc and only takes care of user input and printed output.

*Call graph*: calls 1 internal fn (recalc); 2 external calls (dumps, exit).

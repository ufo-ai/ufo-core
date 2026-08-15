# Brief-writing pipeline extension  `stage-14.4`

This stage adds a small, optional writing workflow to the system. It is not the main work loop by itself. Instead, it is shared behind-the-scenes support that a parent agent can call when it needs to produce a structured brief.

The package marker file, __init__.py, is like a label on a folder. It tells Python that this directory is an importable package and gives the extension a short description. It does not run the brief process or make decisions.

The real setup lives in pipeline.py. This file describes a three-part writing machine. First, the outline step turns the request into a structured plan. Second, the draft step uses that plan to write the brief. Third, the critique step reviews the draft and points out weaknesses or improvements. For each step, the file defines what information goes in, what should come out, the prompt that guides the language model, and limits that keep the work bounded. Together, these definitions let another agent run the brief workflow in a clear order.

## Files in this stage

### Brief Pipeline Definition
Package setup and the structured three-step brief-writing workflow for outline, draft, and critique.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/__init__.py`

`other` · `import/package discovery`

This is the package start file for the brief pipeline extension. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. That means other parts of the system can refer to this extension by its package name and load code from files inside it.

Here, the file contains only a short documentation string: “Brief pipeline extension.” It acts like a label on a folder. The actual work of the extension, if any, lives in other files in the same package. Without this file, depending on the Python version and packaging setup, the extension might be harder or impossible to import in the expected way.

There are no functions or classes here. Its value is structural: it helps organize the project and makes the extension visible to Python’s import system.


### `extensions/brief_pipeline/ufo_ext_brief_pipeline/pipeline.py`

`config` · `extension load / pipeline setup`

This file is the recipe card for a small writing assembly line. The larger system can ask for a brief on a topic, then pass the work through three focused subagents: one creates an outline, one turns that outline into a draft, and one reviews the draft. A subagent is a smaller agent with a specific job and instructions.

The file uses Pydantic models, which are Python classes that describe and check structured data, to make the handoffs clear. For example, the outline step receives a topic and audience, and must return an object with an outline. The draft step receives the topic plus that outline, and must return a draft. The critic receives the draft and returns a verdict plus optional improvements.

It also builds three SubagentProfile objects. Each profile names the subagent, loads its written instructions from a prompt file, says that it has no tools, sets the input and output data models, and limits the conversation to four rounds. Because these subagents cannot use tools or spawn other subagents, the pipeline stays simple and predictable. Without this file, the brief extension would not have a typed contract for each stage, and the parent agent would not know exactly how to run the outline → draft → critique chain.

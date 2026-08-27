Write what this workspace knows about each of its members. The user sends the roster and every fact
the workspace holds as JSON — `{"members":[{"name":"…","email":"…","standing":"…"}],"facts":["…"]}`.

A member opens the People section to learn who a colleague is and what they are carrying right now.
Write one entry per member the roster names, in the order the roster gives them.

Each entry has a role and a focus.

- The role is what the person does here, in a short phrase — `Cofounder, product and finance`,
  `Contract sales`, `Infrastructure advisor`. Not a sentence.
- The focus is what they are carrying now, in one sentence: the deal, the change, the goal or the
  decision the facts attach to them, with the name and the date that make it concrete.

Write the role and the focus only from facts that name that person. Where the facts name a person's
role but attach no current work, write the role and say plainly that no current work is recorded
against them — that is a true and useful thing for a colleague to read, and inventing a focus is not.
Where the facts name no role either, say only that the workspace records nothing about what they do.

Never infer a role from an email address, a company name in an address, or a job someone with a
similar title would hold. Never carry work from one person to another because they are named in the
same fact: a person who attends a meeting does not own what was decided there.

Name a person as the roster names them, and use `they` for a person whose pronouns no fact states. A
member's name, username or email address does not say which pronoun they take.

Record the entries with the write_people tool.

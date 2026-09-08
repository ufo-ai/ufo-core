
update agent
set icon = case icon
    when 'robot' then 'propylon'
    when 'rocket' then 'nabatu'
    when 'bolt' then 'gibil'
    when 'brain' then 'adyton'
    when 'sparkles' then 'dingir'
    when 'bulb' then 'akhet'
    when 'compass' then 'deltoton'
    when 'telescope' then 'aten'
    when 'microscope' then 'omphalos'
    when 'flask' then 'lekythos'
    when 'atom' then 'anthemion'
    when 'code' then 'stele'
    when 'terminal-2' then 'nirah'
    when 'bug' then 'nochtli'
    when 'database' then 'ziggurat'
    when 'server' then 'menhir'
    when 'cloud' then 'nephele'
    when 'mail' then 'carnyx'
    when 'message' then 'osculum'
    when 'calendar' then 'denticulus'
    when 'clock' then 'gnomon'
    when 'checklist' then 'triglyph'
    when 'notebook' then 'acanthus'
    when 'book' then 'ostrakon'
    when 'folder' then 'hydria'
    when 'search' then 'wedjat'
    when 'chart-line' then 'krepis'
    when 'chart-pie' then 'patera'
    when 'receipt' then 'ashnan'
    when 'shopping-cart' then 'kylix'
    when 'users' then 'furcula'
    when 'headset' then 'kalyx'
    when 'lifebuoy' then 'kardia'
    when 'shield' then 'gorgoneion'
    when 'map-pin' then 'thyrsus'
    when 'plane' then 'flabellum'
    when 'briefcase' then 'cedrus'
    when 'palette' then 'sesen'
    when 'pencil' then 'shushan'
    when 'gavel' then 'atef'
end
where icon in (
    'robot', 'rocket', 'bolt', 'brain', 'sparkles', 'bulb', 'compass', 'telescope', 'microscope',
    'flask', 'atom', 'code', 'terminal-2', 'bug', 'database', 'server', 'cloud', 'mail', 'message',
    'calendar', 'clock', 'checklist', 'notebook', 'book', 'folder', 'search', 'chart-line',
    'chart-pie', 'receipt', 'shopping-cart', 'users', 'headset', 'lifebuoy', 'shield', 'map-pin',
    'plane', 'briefcase', 'palette', 'pencil', 'gavel'
);

local mp = require 'mp'
local options = require 'mp.options'
local utils = require 'mp.utils'
local msg = require 'mp.msg'

local o = {
   debug = false,
   playlist_size = 2,
   font_size = 8,
   font_color = "FFFFFF",
   border_size = 1.0,
   border_color = "101010",
   estimates_file = './estimates',
}
options.read_options(o, 'txs')

local video_file_extensions = {'mkv', 'mp4', 'ts', 'avi'}
local samples = {}                -- List of all samples
local samples_to_revisit = {}     -- List of samples that were marked as "equal"
local settings = {}               -- Map file paths to list of encoding settings
local est_times = {}              -- Map file paths to estimated encoding time
local est_sizes = {}              -- Map file paths to estimated final size
local original = nil              -- Source video
local current_playback_position = nil
local longest_settings_string = nil


function dbg(...)
   if o.debug then
      print(...)
   end
end

function info(...)
   msg.info(...)
end

-- Load samples into playlist until it's o.playlist_size long or there are no
-- more samples left.
function fill_playlist(args)
   local args = args or {}
   local playlist_count = mp.get_property_number('playlist-count')
   if playlist_count < o.playlist_size then
      local next_sample = get_next_sample()
      if next_sample ~= nil then
         local function callback(success, result, error)
            fill_playlist(args)
         end
         mp.command_native_async({name='loadfile',
                                  url=next_sample,
                                  flags='append'}, callback)
      elseif #samples == 1 then
         info('###############################################################')
         info('# Best settings:', table.concat(get_all_settings(samples[1]), ':'))
         info('###############################################################')
         show_info()
      end
   else
      dbg('There are already', playlist_count, 'items in the playlist:')
      for i=0,playlist_count-1 do
         dbg('  ' .. mp.get_property('playlist/' .. i .. '/filename'))
      end
      if args.done_callback ~= nil then
         args.done_callback()
      end
   end
end

-- Return next sample that isn't already in the playlist.
function get_next_sample()
   -- If there's only one sample left but some samples were marked as equal,
   -- move them back to samples so they can be reviewed again.
   if #samples <= 1 and #samples_to_revisit > 0 then
      info('Reloading', #samples_to_revisit, 'equal samples:')
      for _,filepath in ipairs(samples_to_revisit) do
         info('  ', filepath)
         table.insert(samples, filepath)
      end
      samples_to_revisit = {}
   end
   for _,filepath in ipairs(samples) do
      if not playlist_contains(filepath) then
         dbg('Next sample:', filepath)
         return filepath
      end
   end
end

-- Remove all samples except for the current, then fill the playlist with more
-- samples.
function declare_better()
   if #samples > 1 then
      local filepath = mp.get_property('path')
      dbg('Declaring better:', filepath)
      for _,filepath_ in playlist_iter() do
         if filepath_ ~= filepath then
            remove_sample({filepath=filepath_})
         end
      end
      info('Best:', filepath)
      fill_playlist({done_callback=playlist_next})
   end
end

-- Remove current video from playlist and `samples`.  Fill the playlist with
-- more samples if there's only one sample left.
function declare_worse(quiet)
   if #samples > 1 then
      local filepath = mp.get_property('path')
      dbg('Declaring worse:', filepath)
      if filepath ~= nil and filepath ~= original then
         remove_sample({filepath=filepath, refill=true})
         info('Worst:', filepath)
      end
   end
end

-- Same as declare_worse(), but also remove sample from file system and
-- estimates file.
function declare_garbage()
   if #samples > 1 then
      local filepath = mp.get_property('path')
      dbg('Declaring garbage:', filepath)
      if filepath ~= nil and filepath ~= original then
         remove_sample({filepath=filepath, refill=true, delete_file=true})
         info('Deleted:', filepath)
      end
   end
end

-- Remove all playlist items and add them to `samples_to_revisit`.
-- fill_playlist() will reload them when `samples` is empty.
function declare_equal()
   if #samples > 1 then
      -- Store current playlist
      local equals = {}
      info('Equals:')
      for _,filepath in playlist_iter() do
         if filepath ~= original then
            dbg('  ', filepath)
            table.insert(equals, filepath)
            table.insert(samples_to_revisit, filepath)
         end
      end
      -- Prevent focus being on last playlist item, maybe by adding another
      -- sample
      prevent_empty_window({force=true})
      -- Remove current playlist, but without the added sample
      for _,filepath in ipairs(equals) do
         remove_sample({filepath = filepath})
      end
      -- Refill playlist
      fill_playlist({done_callback=playlist_next})
   end
end

-- Delete current sample from internal list of samples.
function remove_sample(args)
   local filepath = args.filepath
   local delete_file = args.delete_file or false
   local refill = args.refill or false
   if filepath ~= nil then
      if filepath == original then
         info('Refusing to remove original:', original)
      else
         -- Remove sample from global `samples` list
         dbg('Removing:', filepath)
         for i,filepath_ in ipairs(samples) do
            if filepath_ == filepath then
               table.remove(samples, i)
            end
         end
         dbg(#samples .. ' remaining samples:')
         for k,v in ipairs(samples) do
            dbg(k, v)
         end

         -- Remove sample from mpv's playlist
         prevent_empty_window()
         for i,filepath_ in playlist_iter() do
            if filepath_ == filepath then
               dbg('Removing from playlist:', i, filepath_)
               mp.command_native({'playlist-remove', i})
               break
            end
         end

         if delete_file == true then
            if o.debug then
               dbg('Not deleting sample file while debugging:', filepath)
            else
               -- Remove sample file and its log file
               os.remove(filepath)
               os.remove(path_without_extension(filepath) .. '.log')

               -- Remove estimates for this sample from estimates file
               local diff_settings = table.concat(get_diff_settings(filepath), ':')
               local tmp_est_file = o.estimates_file .. '.tmp'
               local f = io.open(tmp_est_file, 'w')
               for line in io.lines(o.estimates_file) do
                  local parts = split_string(line, '/')
                  if strip_string(parts[1]) ~= diff_settings then
                     f:write(line..'\n')
                  end
               end
               f:close()
               os.rename(tmp_est_file, o.estimates_file)
            end
         end

         if refill == true then
            -- Autofill playlist if there's only one item in it
            if mp.get_property_number('playlist-count') <= 1 then
               dbg('Refilling playlist with single item in it')
               -- Remove all playlist items except for the current one and fill playlist
               -- again.  Select the next item after the last item was added.
               fill_playlist({done_callback=playlist_next})
            end
         end
      end
   end
end

function prevent_empty_window(args)
   local args = args or {}
   -- Before removing the last sample or all samples from the playlist, we must
   -- ensure that the playlist doesn't end after we've done that or mpv will
   -- show a black screen, either for a short time (which is annoying) or until
   -- the user selects another playlist item (even more annoying).
   local playlist_count = mp.get_property_number('playlist-count')
   local playlist_pos_1 = mp.get_property_number('playlist-pos-1')
   if playlist_pos_1 == playlist_count or args.force then
      dbg('############# Preventing empty window ##########')
      local next_sample = get_next_sample()
      dbg('next sample:', next_sample)
      if next_sample ~= nil then
         dbg('loading next sample preemptively:', next_sample)
         mp.command_native({name='loadfile',
                            url=next_sample,
                            flags='append'})
      else
         dbg('setting playlist-pos-1 to', 1)
         mp.set_property_number('playlist-pos-1', 1)
         -- dbg('setting playlist-pos-1 to', playlist_pos_1-1)
         -- mp.set_property_number('playlist-pos-1', playlist_pos_1-1)
      end
      dbg('############# Preventing empty window done ##########')
   end
end

local tmp_playlist = {}
local tmp_playlist_file = os.tmpname()
local toggle_original_playlist_pos = nil
function toggle_original()
   if original ~= nil then
      if mp.get_property('path') ~= original then
         -- Save current playlist
         tmp_playlist = {}
         for _,filepath in playlist_iter() do
            table.insert(tmp_playlist, filepath)
         end
         -- Save playlist position
         toggle_original_playlist_pos = mp.get_property_number('playlist-pos')
         -- Load original and remove all other playlist items
         mp.commandv('loadfile', original)
         mp.commandv('playlist-clear')
      else
         -- Restore previous playlist and position
         -- NOTE: For some reason (race condition?) loading each filepath
         -- individually with 'loadfile' makes it impossible to reliably restore
         -- playlist position.
         f = io.open(tmp_playlist_file, 'w')
         for _,filepath in ipairs(tmp_playlist) do
            f:write(filepath..'\n')
         end
         f:close()
         mp.commandv('loadlist', tmp_playlist_file)
         mp.set_property_number('playlist-pos', toggle_original_playlist_pos)
         os.remove(tmp_playlist_file)
      end
   else
      show_message('No original source specified')
   end
end

-- Play next sample, wrapping around end of the playlist.
function playlist_next()
   local max_pos = mp.get_property_number('playlist-count') - 1
   if max_pos > 0 then
      local cur_pos = mp.get_property_number('playlist-pos')
      local next_pos = max_pos
      if cur_pos ~= nil then
         next_pos = cur_pos + 1
         if next_pos > max_pos then
            next_pos = 0
         end
      end
      dbg('setting next playlist-pos:', next_pos)
      mp.set_property_number('playlist-pos', next_pos)
   end
end

-- Play previous sample, wrapping around beginning of the playlist.
function playlist_prev()
   local max_pos = mp.get_property_number('playlist-count') - 1
   if max_pos > 0 then
      local cur_pos = mp.get_property_number('playlist-pos')
      local prev_pos = 0
      if cur_pos ~= nil then
         prev_pos = cur_pos - 1
         if prev_pos < 0 then
            prev_pos = max_pos
         end
      end
      dbg('setting prev playlist-pos:', prev_pos)
      mp.set_property_number('playlist-pos', prev_pos)
   end
end


-- Event handlers

function store_current_playback_position(event, time_pos)
   if time_pos ~= nil and time_pos > 0 then
      current_playback_position = time_pos
   end
end
mp.observe_property('time-pos', 'native', store_current_playback_position)

function on_file_loaded(event)
   local filepath = mp.get_property('path')
   if filepath ~= nil then
      maybe_seek_to_current_playback_position()
      if info_is_visible() then
         show_info()
      end
   end
end
mp.register_event('file-loaded', on_file_loaded)

function maybe_seek_to_current_playback_position()
   if current_playback_position ~= nil then
      mp.commandv('seek', current_playback_position, 'absolute+exact')
   end
end



-- OSD

local ass_start = mp.get_property_osd("osd-ass-cc/0")
local ass_stop = mp.get_property_osd("osd-ass-cc/1")
local redraw_timer = mp.add_periodic_timer(1, function() end)
redraw_timer:kill()

-- Display permanent message
function show_overlay(msg)
   if redraw_timer ~= nil then
      redraw_timer:stop()
   end
   local redraw_delay = 3
   local data = string.format("%s{\\r}{\\fs%d}{\\fnMonospace}{\\bord%f}{\\3c&H%s&}" ..
                                 "{\\1c&H%s&}%s%s",
                              ass_start, o.font_size, o.border_size, o.border_color,
                              o.font_color, msg, ass_stop)
   local function redraw()
      mp.osd_message(data, redraw_delay)
   end
   redraw()
   redraw_timer = mp.add_periodic_timer(redraw_delay, redraw)
end

-- Display temporary message for three seconds
function show_message(msg, duration)
   local duration = duration or 3
   if info_is_visible() then
      hide_info()
      mp.osd_message(msg, duration)
      mp.add_timeout(duration, show_info)
   else
      mp.osd_message(msg, duration)
   end
end

-- Hide permanent message
function hide_info()
   if redraw_timer ~= nil then
      redraw_timer:stop()
      redraw_timer = nil
      mp.osd_message('')
   end
end

-- Show permanent message
function show_info()
   local filepath = mp.get_property('path')
   local msg = ''
   if filepath == original then
      msg = '→Original\n\n'
   end
   if filepath ~= nil then
      local diff_settings = get_diff_settings(filepath)
      if diff_settings ~= nil then
         local id = table.concat(diff_settings, ':')
         if #samples > 1 then
            msg = string.format('%s%s samples left', msg, #samples + #samples_to_revisit)
            if #samples_to_revisit > 0 then
               msg = string.format('%s (%s of equal quality)', msg, #samples_to_revisit)
            end
            msg = string.format('\n%s\n\nCurrent samples:', msg)
            for i=0,mp.get_property_number('playlist-count')-1 do
               local filepath_ = mp.get_property('playlist/' .. i .. '/filename')
               local filepath_id = table.concat(get_diff_settings(filepath_), ':')
               local filepath_id_len = get_longest_settings()
               local est_time = est_times[filepath_id] or 'unknown'
               local est_size = est_sizes[filepath_id] or 'unknown'
               if filepath_id == id then
                  msg = string.format('%s\n  →%s / %s / %s', msg,
                                      rpad_string(filepath_id, filepath_id_len),
                                      est_time, est_size)
               else
                  msg = string.format('%s\n   %s / %s / %s', msg,
                                      rpad_string(filepath_id, filepath_id_len),
                                      est_time, est_size)
               end
            end
         else
            msg = string.format('%s\nBest settings: %s', msg, id)
         end
      end
   end
   show_overlay(msg)
end

function toggle_info()
   if redraw_timer == nil then
      show_info()
   else
      hide_info()
   end
end

function info_is_visible()
   if redraw_timer == nil then
      return false
   else
      return true
   end
end

function get_longest_settings()
   if longest_settings_string == nil then
      longest_settings_string = 0
      for _,filepath in pairs(samples) do
         local settings = get_diff_settings(filepath)
         local settings_str = table.concat(settings, ':')
         local len = string.len(settings_str)
         if len > longest_settings_string then
            longest_settings_string = len
         end
      end
   end
   return longest_settings_string
end



--- Utilities

function is_sample(filepath)
   if filepath ~= nil then
      local dir, filename = utils.split_path(filepath)
      -- Example.sample@5:00-30.me=umh:deblock=-2,-2:trellis=2.mkv
      if string.find(filename, '%.sample@[%d:-]+%-[%d:]+%.') then
         return true
      end
   end
   return false
end

function is_original(filepath)
   if filepath ~= nil then
      local dir, filename = utils.split_path(filepath)
      -- Example.original@5:00-10.mkv
      if string.find(filename, '%.original@[%d:-]+%-[%d:]+%.mkv$') then
         return true
      end
   end
   return false
end

function playlist_contains(filepath)
   for _,filepath_ in playlist_iter() do
      if filepath_ == filepath then
         return true
      end
   end
   return false
end

-- Iterate over file paths in the playlist.
function playlist_iter()
   local filepaths = {}
   local i = 0
   local filepath = mp.get_property('playlist/' .. i .. '/filename')
   while filepath ~= nil do
      table.insert(filepaths, filepath)
      i = i + 1
      filepath = mp.get_property('playlist/' .. i .. '/filename')
   end
   i = -1
   return function()
      if #filepaths > 0 then
         local filepath = filepaths[1]
         table.remove(filepaths, 1)
         i = i + 1
         return i, filepath
      end
   end
end

-- Find settings that are not the identical in any other sample.
function get_diff_settings(filepath)
   local s = settings[filepath]
   if s ~= nil then
      local diff = {}
      for i=1,#s do
         for _,s_ in pairs(settings) do
            if s[i] ~= s_[i] then
               table.insert(diff, s[i])
               break
            end
         end
      end
      return diff
   end
end

function get_all_settings(filename)
   -- Example.sample@5:00-30.me=umh:deblock=-2,-2:trellis=2.mkv
      if is_sample(filename) then
      local s = string.gsub(filename, '%.([%a%d]+)$', '')     -- Remove file extension
      s = string.gsub(s, '^.*%.sample@([%d:]+)%-[%d:]+%.', '') -- Remove title
      return split_string(s, ':')
   end
end

-- https://stackoverflow.com/a/7615129
function split_string(str, sep)
   local sep = sep or '%s'
   local t = {}
   for s in string.gmatch(str, '([^' .. sep .. ']+)') do
      table.insert(t, s)
   end
   return t
end

function strip_string(str)
   local s = string.gsub(str, '^%s+', '')
   s = string.gsub(s, '%s+$', '')
   return s
end

function rpad_string(str, len)
   while string.len(str) < len do
      str = str .. ' '
   end
   return str
end

function path_without_extension(path)
  return path:match("^(.+)%.%w-$") or path
end



--- Initialization

-- Find original source video.
function find_original()
   local dir = mp.get_property('working-directory')
   for _,filename in ipairs(utils.readdir(dir)) do
      if is_original(filename) then
         original = utils.join_path(dir, filename)
         dbg('Found original:', original)
         break
      end
   end
end

-- Find sample files.
function find_samples()
   local dir = mp.get_property('working-directory')
   for _,filename in ipairs(utils.readdir(dir)) do
      local ext = filename:match('%.([%a%d]+)$')
      for _,ext_ in ipairs(video_file_extensions) do
         if ext_ == ext and is_sample(filename) then
            table.insert(samples, utils.join_path(dir, filename))
         end
      end
   end
   table.sort(samples)
   info('Found', #samples, 'samples')
   -- for k,v in pairs(samples) do
   --    dbg(k, v)
   -- end
end

-- Read encoding settings from sample filenames.
function find_settings()
   for _,filepath in pairs(samples) do
      settings[filepath] = get_all_settings(filepath)
   end
end

-- Read time and size estimates from estimates file.
function read_estimates()
   local dir = mp.get_property('working-directory')
   local filepath = utils.join_path(dir, o.estimates_file)
   -- Check if filepath exists first
   local f = io.open(filepath, "r")
   if f then
      -- io.lines() wants a file path, not a handle
      f:close()
      for line in io.lines(filepath) do
         local parts = split_string(line, '/')
         if #parts >= 4 then
            local s = parts[1]:gsub("^%s*(.-)%s*$", "%1")
            est_times[s] = parts[2]:gsub("^%s*(.-)%s*$", "%1")
            est_sizes[s] = parts[4]:gsub("^%s*(.-)%s*$", "%1")
         end
      end
   end
end

-- Keybindings and properties
mp.add_key_binding('j', 'playlist-next', playlist_next)
mp.add_key_binding('k', 'playlist-prev', playlist_prev)
mp.add_key_binding('b', 'sample-is-better', declare_better)
mp.add_key_binding('w', 'sample-is-worse', declare_worse)
mp.add_key_binding('e', 'samples-are-equal', declare_equal)
mp.add_key_binding('shift+w', 'sample-is-garbage', declare_garbage)
mp.add_key_binding('o', 'toggle-original', toggle_original)
mp.add_key_binding('`', 'toggle-info', toggle_info)

if not o.debug then
   mp.set_property('fullscreen', 'yes')
end
mp.set_property('force-window', 'yes')
mp.set_property('msg-level', 'all=no,txs_compare=trace')
mp.set_property('pause', 'yes')
mp.set_property('mute', 'yes')
mp.set_property('loop-file', 'yes')
mp.set_property('osd-fractions', 'yes')
mp.set_property('osd-level', 2)

find_original()
find_samples()
find_settings()
read_estimates()
fill_playlist()

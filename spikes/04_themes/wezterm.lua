-- Spike-only WezTerm config. Run with:
--   wezterm --config-file spikes/04_themes/wezterm.lua start
-- The two lines marked SNIPPET are all a real wezterm.lua needs (with the module
-- placed in ~/.config/wezterm/, which is already on package.path).
local wezterm = require 'wezterm'
package.path = wezterm.config_dir .. '/?.lua;' .. package.path

local config = wezterm.config_builder()
config.color_scheme = 'Tokyo Night' -- default for non-project dirs

require('cardak_projects').apply(config) -- SNIPPET

return config

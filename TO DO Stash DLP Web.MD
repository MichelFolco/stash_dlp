[x] Hide the terminal window completely when starting dl, prevent the window from popping in front of the UI.
[x] Implement audio only dl and audio files display in ledger. 
[x] support for MP3 play back
[x] Remember the last 10 selected download paths, they can be selected in the change path dialog. 
[x] Add Text box for filtering the ledger.  Controls to sort by date added, file size, filename.  On top of ledger.
[x] Option to Move Files from download folder to target folder. Target folder are defined in the same menu as the download folder, remembers recent target folders too and is path is persistant.  When the option is selected from the click menu, the file is moved from download to target path.  There is also a button on the same row as refresh and clear to transfer all files to target (confirm message before execution)
[x] Display free space size for the drive of each saved folder.
[x] Create a folder structure where all the Stash DLP files are saved and stored (settings, thumbnails, history etc) to avoid spamming the root download folder.
[x] Remembers where the playback was at and picksup where it was left.
[x] start video when clicking on the thumbnail.
[x] Found a bug.  The first item i play and stop after booting the program gets stuck in memory and I cannot delete it.  Windows tells me it is used by python.  I gets stuck in that state unless I close stash DLP.
[x] redo the refresh button routine.  When pressed it should scan the folder and remove missing files from the ledger and add new files present in the folder that are not in the ledger.
[x] add ffmpeg re encoding support.  choose resolution, bit rate technique, force aspect ratio, de interlace, 
 

[x] filter downloads by audio only
[x] encode setup screen should be scrolled to the top when opened.
[x[ Refresh Video list on top of New Encode Job Menu when I press "New Encode Job".
[x] Dont append (H.265 or anything else to encoded file names saved in converted, keep the exact same file name as the original.
[x] When i activate the force aspect ratio in encode setup menu, 16:9 is selected by default but the resolution is not updated to 16:9, the user have to click on the already selected 16:9 button to update the target width.  It should be the target width of 16:9 already before the option is ticked.
[x] Play a sound when an encoding job is completed


[x] Add Retry button on download cards of failed downloads 
[x] Make the failed download cards persistent to the queue until the user manually deletes it.  Fails should be loaded in the queue even when no file is in the download folder. Dont go and display all the failed downloads recorded in a history file though.  Persistence starts from now on, no retrocation.
[x] Show full path on the download folder display on the main ui.  format is DL: c:/path/download.
[x] When I click on the download folder display on main ui, pop a menu below the folder display that list the saved dl folders the use can choose to change the current DL folder.  Same for Target folder.
[x] display the download and target folders display on the main ui on the same line  


[x] Remove minimode button and code and replace it with a gear icon that will open the logo right click menu.  Options floating menu cannot be accessed via right click anymore, only by clicking the gear. 
[x] Display the current download folder and target folders name and available space on the main UI.  Clicking on it brings the folder manager UI.  Gets its own row but should be discrete and not too prominent.
[x] Let me copy the file name and link of a file that is still downloading (add option to current menu with cancel)
[show the download path in the download cards (during download and once it is complete of failed)
[x] Consolidate the thumbnails, download history, thumbnails and settings to a subfolders in the app location and not spread across all download settings.  Would imply tracking the download folder for each download as well as having multiple per folder queue files.  I want to avoid creating a bunch of files in every download folder but I also want to preserve the compartmentalization of each download location. Download mode:  Load only files that are present in the set download folder queue.  History file:  centralized but tracks the download location, only one file.  Find Link Mode:  per folder, searches the history of only the set folder.  Encoding mode: per folder, only loads the queue of the 

[x] Revamp Find link mode.  Renamed to Search History Mode.  When activated, the download history of the set download folder is loaded in the ledger.  Every download gets a card.  Ledger can be searched and filterd using the current controls.  Per card options (click menu) are: Copy link, copy name, open location (windows only, not available if other client), delete from history.
[x] Make Auto M3U sniffing after a failed download optional (toggle in settings)
[x] Add a way for the user to pass arguments to yt-DLP
[ ] When I rename a file, is the history file updated?
[x] move the custom yt-dlp argument button to the left of the settings button.
[ ] add drag and drop support to the download cards where i can drop the cards directly in an editing program DONT WORK with losslesscut.
[x] Add Stash Import pill to cards for videos that were copied from stash.
[ ] Add Synchronize Audio option to card click menu:  loads the video in a Synch UI.  The UI has a Video player.  Controls to enter audio delay (0.000s), typed or dials. These tips are displayed on synch ui:
-Audio plays too early (leads video): Enter a positive number (e.g., 500 delays audio by 500 ms).
-Audio plays too late (lags video): Enter a negative number (e.g., -500 advances audio by 500 ms).
Apply Delay button -> ffmpeg reencodes with delay, creates the syched file in converted subfolder, new video is loaded in synch ui player. feedback to user that new vid was loaded.
-user retweaks, Apply again ->  ovewrite previous render.  When user is satisfied, user confirms synch with another button.  The Synchronized pill is applied to the card.  Same logic as re-encodes applies to the transfer to target or replace stash source if applicable.   
[ ]Add Stash section to settings:  API key.
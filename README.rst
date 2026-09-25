spotify_to_ytmusic
####################

.. |pypi-downloads| image:: https://img.shields.io/pypi/dm/spotify_to_ytmusic?style=flat-square
    :alt: PyPI Downloads
    :target: https://pypi.org/project/spotify_to_ytmusic/

.. |discuss| image:: https://img.shields.io/github/discussions/sigma67/spotify_to_ytmusic?style=flat-square
   :alt: Ask questions at Discussions
   :target: https://github.com/sigma67/spotify_to_ytmusic/discussions

.. |code-coverage| image:: https://img.shields.io/codecov/c/github/sigma67/spotify_to_ytmusic?style=flat-square
    :alt: Code coverage
    :target: https://codecov.io/gh/sigma67/spotify_to_ytmusic

.. |latest-release| image:: https://img.shields.io/github/v/release/sigma67/spotify_to_ytmusic?style=flat-square
    :alt: Latest release
    :target: https://github.com/sigma67/spotify_to_ytmusic/releases/latest

.. |commits-since-latest| image:: https://img.shields.io/github/commits-since/sigma67/spotify_to_ytmusic/latest?style=flat-square
    :alt: Commits since latest release
    :target: https://github.com/sigma67/spotify_to_ytmusic/commits


|pypi-downloads| |discuss| |code-coverage| |latest-release| |commits-since-latest|

A simple command line script to clone a Spotify playlist to YouTube Music.

- Transfer a single Spotify playlist
- Like all the songs in a Spotify playlist
- Update a transferred playlist on YouTube Music
- Transfer all playlists for a Spotify user
- Like all songs from all playlists for a Spotify user
- Remove playlists from YouTube Music


Web UI (both directions)
------------------------

This fork adds a local web UI that moves your library **in either direction**
(Spotify → YouTube Music and YouTube Music → Spotify):

- Playlists you own or collaborate on
- Liked songs
- Saved albums
- Followed artists

Re-running a transfer only adds what the other side is missing, and matches are
cached, so it doubles as a manual sync. Songs that couldn't be matched are listed
at the end and can be downloaded as a text file.

.. code-block::

    pipx install "spotify_to_ytmusic[web] @ git+https://github.com/yonkric/spotify_to_ytmusic"
    spotify_to_ytmusic web

Then open http://127.0.0.1:8765 and follow the three steps on the page:

1. **Spotify** – create an app at https://developer.spotify.com/dashboard (requires
   Spotify Premium), tick *Web API*, add the redirect URI
   ``http://127.0.0.1:8765/callback/spotify`` and paste the app's Client ID. No client
   secret is needed (PKCE).
2. **YouTube Music** – in Chrome, open music.youtube.com while signed in, open DevTools →
   Network, filter for ``browse``, right-click a request → *Copy → Copy as cURL* and paste it.
   This contains your login cookies; it is stored only on your computer
   (in the ``spotify_to_ytmusic/web`` folder of your user cache directory).
3. **Transfer** – choose a direction, tick what to move and start.

Limitations, imposed by the services rather than this tool:

- Playlist *folders* can't be transferred: Spotify's API doesn't expose them and YouTube
  Music has no folders.
- Since February 2026 Spotify only returns the songs of playlists you own or collaborate on,
  so playlists you merely follow can't be copied.
- A Spotify development-mode app is limited to 5 users, which is fine for personal use.
- YouTube Music access uses the unofficial `ytmusicapi <https://github.com/sigma67/ytmusicapi>`_.
  If your pasted login expires, disconnect and paste a fresh one.

Install (command line)
----------------------

- Python 3.10 or later - https://www.python.org
- pipx - https://pipx.pypa.io

.. code-block::

    pipx ensurepath

- Open a new shell. Install:

.. code-block::

    pipx install spotify_to_ytmusic


Setup
-------

1. Generate a new app at https://developer.spotify.com/dashboard
2. Generate a new app by following instructions at https://ytmusicapi.readthedocs.io/en/stable/setup/oauth.html
3. Run

.. code-block::

    spotify_to_ytmusic setup

For backwards compatibility you can also create your own file and pass it using ``--file settings.ini``.

If you want to transfer private playlists from Spotify (i.e. liked songs), choose "yes" for oAuth authentication, otherwise choose "no".
For oAuth authentication you should set ``https://127.0.0.1`` as redirect URI for your app in Spotify's developer dashboard.

Usage
------

After you've completed setup, you can simply run the script from the command line using:

.. code-block::

    spotify_to_ytmusic create <spotifylink>

where ``<spotifylink>`` is a link like https://open.spotify.com/playlist/0S0cuX8pnvmF7gA47Eu63M

The script will log its progress and output songs that were not found in YouTube Music to **noresults_youtube.txt**.

Transfer all playlists of a Spotify user
----------------------------------------

For migration purposes, it is possible to transfer all of your own playlists by using your Spotify user ID (unique username).
Since Spotify's February 2026 API changes, only the authenticated user's playlists can be listed, so this requires oAuth authentication.

.. code-block::

    spotify_to_ytmusic all <spotifyuserid>

Transfer liked tracks of the Spotify user
-----------------------------------------

**You must use oAuth authentication for transferring liked songs.**

.. code-block::

   spotify_to_ytmusic liked

This command will open browser where you should give access to your account (if you haven't done that before).
After authorization you will be redirected to 127.0.0.1, copy link you were redirected to (looks like 127.0.0.1/?code=...) and paste to command line.

Command line options
---------------------

There are some additional command line options for setting the playlist name and determining whether it's public or not. To view them, run

.. code::

    spotify_to_ytmusic -h


To view subcommand help, run i.e.

.. code-block::

    spotify_to_ytmusic setup -h


Available subcommands:

.. code-block::

    positional arguments:
      {setup,create,update,remove,all}
                            Provide a subcommand
        setup               Set up credentials
        create              Create a new playlist on YouTube Music.
        update              Delete all entries in the provided Google Play Music playlist and update the playlist with entries from the Spotify playlist.
        remove              Remove playlists with specified regex pattern.
        all                 Transfer all public playlists of the specified user (Spotify User ID).

    options:
      -h, --help            show this help message and exit

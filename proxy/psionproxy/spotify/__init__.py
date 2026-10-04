"""Spotify for the PsionLX netBook Pro.

The netBook Pro cannot speak to Spotify itself -- its TLS stops at 1.0 and no
Spotify client was ever built for its 2005 ARM userspace -- so PsionNet does
the Spotify half on the Mac and hands the device plain HTTP:

  librespot        a Spotify Connect speaker called "netBook Pro", run here,
                   whose audio comes out as raw PCM (audio.py, librespot.py)
  lameenc          that PCM encoded to MP3, which the device's GStreamer
                   already decodes, served at /spotify/stream.mp3
  Web API          search, playlists, library and playback control, using
                   the user's own Spotify developer Client ID (webapi.py)
  routes.py        a small line-based text API the PsionLX app reads

Needs Spotify Premium: librespot cannot play for a free account.
"""

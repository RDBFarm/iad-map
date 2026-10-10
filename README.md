# live-data

Written by the ADS-B receiver at Red Devil Bison Farm every 15 minutes and
force-pushed, so this branch only ever holds one commit: the latest 24 hours.
`live-data-prev` holds the same files on top of the previous push, only so
that git uploads just what changed. Do not edit either; the next push
replaces them. The code that writes it is in
`pi/` on `main`, and `live.html` on `main` is the page that reads it.

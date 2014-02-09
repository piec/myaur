myaur
=====

`myaur` is a python3 implementation of archlinux [AUR rpc](https://wiki.archlinux.org/index.php/AurJson) which lets you host custom [aur](https://aur.archlinux.org/)-like repositories. `myaur` generates archlinux source packages from `PKGFILE`s on the fly.

Put your custom repositories in the `overlays/` directory and they will be accessible with the standard AUR rpc at url `http://[location]/aur/[repository-name]/rpc.php`.

If your custom repository is a github project, `myaur` can follow commits using github's WebHooks. Direct hooks to `http://[location]/aur/github-hook`. This will make `myaur` update its local copy of the repository automatically.


### Howto
Clone and run `myaur`:
```bash
  # install python dependencies
$ pip3 install --user colorama bottle sh
  # clone myaur
$ git clone https://github.com/piec/myaur.git && cd myaur
  # clone your custom aur repository into overlays/
$ git clone https://github.com/piec/aur-overlay.git overlays/aur-overlay
  # start myaur (dev hosting)
$ ./aur.py
Listening on http://localhost:8080/
```
Use `yaourt` to install a package from your custom repository:  
**Note**: currently you need [my patched version of yaourt](https://github.com/piec/yaourt) ([PKGBUILD](https://raw.github.com/piec/aur-overlay/master/yaourt-git/PKGBUILD)) for the `--aur-url` support
```bash
$ yaourt --aur-url http://localhost:8080/aur/aur-overlay -S st-git
```
Or using an alias:
```bash
$ alias myaourt='yaourt --aur-url http://localhost:8080/aur/aur-overlay'
$ myaourt ...
```

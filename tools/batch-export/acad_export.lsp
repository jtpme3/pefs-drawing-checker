;;; acad_export.lsp
;;; Headless DXF + PDF export for one drawing, run by accoreconsole.exe.
;;; Driven entirely by environment variables so no per-drawing script is needed:
;;;   ACX_DXF  output folder for .dxf   (blank = skip DXF)
;;;   ACX_PDF  output folder for .pdf   (blank = skip PDF)
;;;   ACX_CTB  plot style table to force, e.g. "monochrome.ctb" (blank = use the
;;;            one saved in each layout's page setup)
;;;
;;; Progress is printed as ACX>... lines, which the PowerShell driver greps.

;; Paper-space layouts in tab order. layoutlist is not defined in accoreconsole,
;; so read the ACAD_LAYOUT dictionary directly.
(defun acx-layouts ( / lst nm ed)
  (setq lst '())
  (foreach e (dictsearch (namedobjdict) "ACAD_LAYOUT")
    (if (= 3 (car e)) (setq nm (cdr e)))
    (if (and (= 350 (car e)) nm)
      (progn
        (setq ed (entget (cdr e)))
        (if (/= (strcase nm) "MODEL")
          (setq lst (cons (cons (cons nm (cdr e)) (cdr (assoc 71 ed))) lst)))
        (setq nm nil)
      )
    )
  )
  (mapcar 'car (vl-sort lst '(lambda (a b) (< (cdr a) (cdr b)))))
)

;; Force a plot style table onto a layout by editing DXF group 7 of the LAYOUT
;; object (the "current style sheet" of its embedded plot settings).
(defun acx-set-ctb (ename ctb / ed)
  (setq ed (entget ename))
  (entmod
    (if (assoc 7 ed)
      (subst (cons 7 ctb) (assoc 7 ed) ed)
      (append ed (list (cons 7 ctb)))))
)

(defun ACX-EXPORT ( / dxfdir pdfdir ctb base lays pair lay pdfpath dxfpath n)
  (setq dxfdir (getenv "ACX_DXF")
        pdfdir (getenv "ACX_PDF")
        ctb    (getenv "ACX_CTB")
        base   (vl-filename-base (getvar "DWGNAME"))
        lays   (acx-layouts))

  (setvar "FILEDIA" 0)          ; suppress file dialogs
  (setvar "CMDDIA"  0)
  (setvar "BACKGROUNDPLOT" 0)   ; plot in the foreground so the process blocks until done
  (princ (strcat "\nACX>LAYOUTS " (itoa (length lays))))

  ;;; ---------------- PDF ----------------
  (if (and pdfdir (/= pdfdir ""))
    (foreach pair lays
      (setq lay (car pair))
      ;; single layout -> plain drawing name; multiple -> suffix with layout name
      (setq n (if (= 1 (length lays)) base (strcat base "-" lay)))
      (setq pdfpath (strcat pdfdir "\\" n ".pdf"))
      (if (findfile pdfpath) (vl-file-delete pdfpath))
      (if (and ctb (/= ctb "")) (acx-set-ctb (cdr pair) ctb))
      (princ (strcat "\nACX>PLOT [" lay "] -> " pdfpath))
      (setvar "CTAB" lay)
      ;; -PLOT prompts: detailed? / layout / page setup / device / file / save page setup / proceed
      (if (vl-catch-all-error-p
            (vl-catch-all-apply
              '(lambda ()
                 (command "_.-PLOT" "_N" lay "" "DWG To PDF.pc3" pdfpath "_N" "_Y"))))
        (princ "\nACX>PLOT-FAIL")
        (princ (if (findfile pdfpath) "\nACX>PLOT-OK" "\nACX>PLOT-NOFILE")))
    )
  )

  ;;; ---------------- DXF ----------------
  ;; Done last: SAVEAS renames the in-memory drawing to the .dxf.
  (if (and dxfdir (/= dxfdir ""))
    (progn
      (setq dxfpath (strcat dxfdir "\\" base ".dxf"))
      (if (findfile dxfpath) (vl-file-delete dxfpath))
      (princ (strcat "\nACX>DXF -> " dxfpath))
      ;; SAVEAS prompts: format / decimal places / filename
      (if (vl-catch-all-error-p
            (vl-catch-all-apply
              '(lambda () (command "_.SAVEAS" "_DXF" "16" dxfpath))))
        (princ "\nACX>DXF-FAIL")
        (princ (if (findfile dxfpath) "\nACX>DXF-OK" "\nACX>DXF-NOFILE")))
    )
  )
  (princ "\nACX>END")
  (princ)
)

(ACX-EXPORT)

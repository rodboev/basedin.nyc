<?php
header('Content-type: text/html; charset=utf-8');

function auto_version($file) {
    if(strpos($file, '/') !== 0 || !file_exists($_SERVER['DOCUMENT_ROOT'] . $file))
        return $file;
 
    $mtime = filemtime($_SERVER['DOCUMENT_ROOT'] . $file);
    return preg_replace('{\\.([^./]+)$}', ".\$1?$mtime", $file);
}
?>
<!DOCTYPE html>
<html>
<head>
    <title>Rod Boev: Resume and Recommendations</title>
    <link rel="stylesheet" href= "<?php echo auto_version('/assets/style.css'); ?>" />
    <link rel="mask-icon" href="<?php echo auto_version('/safari-pinned-tab.svg'); ?>" color="white" />
    <link rel="apple-touch-icon" sizes="180x180" href="<?php echo auto_version('/apple-touch-icon.png'); ?>" />
    <link rel="manifest" href="<?php echo auto_version('/manifest.json'); ?>" />
    <meta name="msapplication-TileColor" content="black" />
    <meta name="description" content="Front-end and full stack web development. E-commerce. Digital marketing. Hands-on technical lead. Technology strategy." />
    <meta property="og:image" content="<?php echo auto_version('/share.png'); ?>" />
</head>
<body>

<a href="resume/"><span><span>Resume</span></span></a>
<a href="recommendations/"><span><span>Recommendations</span></span></a>

<script src="<?php echo auto_version('/pdfjs-dist-master/build/pdf.min.js'); ?>"></script>
<script src="<?php echo auto_version('/assets/script.js'); ?>"></script>
</body>
</html>

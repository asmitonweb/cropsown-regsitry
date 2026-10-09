const fs = require('fs');
const path = require('path');

// Record cards show the record image, or an empty grey box when there is none.
// Replace that with an <img> that always renders: the record image (MinIO's
// in-cluster host rewritten to the published port), falling back to the default
// silhouette when there is no image or it fails to load.
//
// Matched by structure, not by exact text, because the minifier's names differ
// between platform versions: the expression lived inline in the register page
// (src:f.record_image_url) up to 0.0.0-develop.296 and moved into the shared
// record card component (src:<local>) in openg2p-registry 1.2.
const chunkDir = '/app/.next/static/chunks';
const SIZE = 'w-12 h-12 sm:w-14 sm:h-14 lg:w-16 lg:h-16';
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const PATTERN = new RegExp(
    '([\\w.]+)\\?\\(0,(\\w+)\\.jsx\\)\\("img",\\{src:\\1,alt:[^,]+,className:"' + esc(SIZE) +
    ' rounded-md object-cover shrink-0"\\}\\):\\(0,\\2\\.jsx\\)\\("div",\\{className:"' + esc(SIZE) +
    ' bg-secondary-third rounded-md shrink-0"\\}\\)',
    'g'
);

function replacement(_match, src, jsx) {
    return '(0,' + jsx + '.jsx)("img",{src:(' + src + '?' + src +
        '.replace("minio:9000","localhost:9022"):"/images/register/profile.png"),' +
        'onError:e=>{e.target.onerror=null;e.target.src="/images/register/profile.png"},' +
        'alt:"",className:"' + SIZE + ' rounded-md object-cover shrink-0"})';
}

let patched = 0;
function walkDir(dir) {
    for (const file of fs.readdirSync(dir)) {
        const filePath = path.join(dir, file);
        if (fs.statSync(filePath).isDirectory()) {
            walkDir(filePath);
        } else if (filePath.endsWith('.js')) {
            const content = fs.readFileSync(filePath, 'utf8');
            const updated = content.replace(PATTERN, replacement);
            if (updated !== content) {
                fs.writeFileSync(filePath, updated, 'utf8');
                patched += 1;
                console.log(`Successfully patched: ${filePath}`);
            }
        }
    }
}

walkDir(chunkDir);
if (patched === 0) {
    console.error('Target string not found in any chunks!');
    process.exit(1);
}

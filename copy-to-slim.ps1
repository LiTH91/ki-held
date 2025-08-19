# Copy ki-held to ki-held-slim excluding large files and build artifacts

$sourceDir = "."
$targetDir = "../ki-held-slim"

# Remove target directory if it exists and recreate
if (Test-Path $targetDir) {
    Remove-Item -Recurse -Force $targetDir
}
New-Item -ItemType Directory -Path $targetDir

# Directories and files to exclude
$excludePatterns = @(
    "**\venv\**",
    "**\.venv\**", 
    "**\node_modules\**",
    "**\target\**",
    "**\__pycache__\**",
    "**\.git\**",
    "**\logs\**",
    "**\*.log",
    "**\*.exe",
    "**\*.pdb",
    "**\*.dll",
    "**\*.lib",
    "**\*.rlib",
    "**\*.rmeta",
    "**\vs_buildtools.exe",
    "**\rustup-init.exe",
    "**\evidence\temp\**",
    "**\*.egg-info\**",
    "**\.env",
    "**\*.tmp",
    "**\*.temp"
)

# Function to check if path should be excluded
function ShouldExclude($path) {
    foreach ($pattern in $excludePatterns) {
        if ($path -like $pattern) {
            return $true
        }
    }
    return $false
}

# Copy files recursively, excluding large/build artifacts
function CopySelectively($source, $destination) {
    if (!(Test-Path $source)) { return }
    
    Get-ChildItem -Path $source -Recurse | ForEach-Object {
        $relativePath = $_.FullName.Substring($source.Length + 1)
        $targetPath = Join-Path $destination $relativePath
        
        if (!(ShouldExclude $relativePath)) {
            if ($_.PSIsContainer) {
                # Create directory
                if (!(Test-Path $targetPath)) {
                    New-Item -ItemType Directory -Path $targetPath -Force | Out-Null
                }
            } else {
                # Copy file
                $targetDir = Split-Path $targetPath -Parent
                if (!(Test-Path $targetDir)) {
                    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
                }
                Copy-Item $_.FullName $targetPath -Force
                Write-Host "Copied: $relativePath" -ForegroundColor Green
            }
        } else {
            Write-Host "Excluded: $relativePath" -ForegroundColor Yellow
        }
    }
}

Write-Host "Starting selective copy to ki-held-slim..." -ForegroundColor Cyan

# Copy root files first (excluding patterns)
Get-ChildItem -Path $sourceDir -File | ForEach-Object {
    if (!(ShouldExclude $_.Name)) {
        Copy-Item $_.FullName (Join-Path $targetDir $_.Name) -Force
        Write-Host "Copied: $($_.Name)" -ForegroundColor Green
    } else {
        Write-Host "Excluded: $($_.Name)" -ForegroundColor Yellow  
    }
}

# Copy directories selectively
Get-ChildItem -Path $sourceDir -Directory | ForEach-Object {
    if (!(ShouldExclude $_.Name)) {
        $targetSubDir = Join-Path $targetDir $_.Name
        New-Item -ItemType Directory -Path $targetSubDir -Force | Out-Null
        CopySelectively $_.FullName $targetSubDir
    } else {
        Write-Host "Excluded directory: $($_.Name)" -ForegroundColor Yellow
    }
}

Write-Host "Copy complete!" -ForegroundColor Green

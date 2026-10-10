Description: Restricted native registry keys; no application conversation content
Author: Anonymous artifact collection
Version: 1.0
Id: 97b82a85-f6ec-4320-b663-275d0ee39677
Keys:
    -
        Description: BAM native last execution records
        HiveType: SYSTEM
        Category: Program Execution
        KeyPath: ControlSet*\Services\BAM\State\UserSettings\*
        Recursive: false
    -
        Description: BAM legacy native records
        HiveType: SYSTEM
        Category: Program Execution
        KeyPath: ControlSet*\Services\BAM\UserSettings\*
        Recursive: false
    -
        Description: DAM native records
        HiveType: SYSTEM
        Category: Program Execution
        KeyPath: ControlSet*\Services\DAM\State\UserSettings\*
        Recursive: false
    -
        Description: Appx package registration
        HiveType: SOFTWARE
        Category: Installation
        KeyPath: Microsoft\Windows\CurrentVersion\Appx\AppxAllUserStore\Applications
        Recursive: true
    -
        Description: Local account names
        HiveType: SAM
        Category: Accounts
        KeyPath: SAM\Domains\Account\Users\Names
        Recursive: true
    -
        Description: PCA native store
        HiveType: NTUSER
        Category: Program Execution
        KeyPath: Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Compatibility Assistant\Store
        Recursive: false
    -
        Description: User Appx repository
        HiveType: UsrClass
        Category: Installation
        KeyPath: Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository
        Recursive: true
    -
        Description: Package user Appx registration
        HiveType: NTUSER
        Category: Installation
        KeyPath: Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository
        Recursive: true
    -
        Description: Native common-dialog file references
        HiveType: NTUSER
        Category: File references
        KeyPath: Software\Microsoft\Windows\CurrentVersion\Explorer\ComDlg32
        Recursive: true
    -
        Description: Native recent-document references
        HiveType: NTUSER
        Category: File references
        KeyPath: Software\Microsoft\Windows\CurrentVersion\Explorer\RecentDocs
        Recursive: true
    -
        Description: Native recycle-bin settings
        HiveType: NTUSER
        Category: File references
        KeyPath: Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket
        Recursive: true

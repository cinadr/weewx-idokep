# Installer for IDOKEP restx extension
# Zsolt Zimmerman, 2020

from setup import ExtensionInstaller

def loader():
    return IDOKEPInstaller()

class IDOKEPInstaller(ExtensionInstaller):
    def __init__(self):
        super(IDOKEPInstaller, self).__init__(
            version="0.1",
            name='Idokep',
            description='IDOKEP data uploader',
            author="Zimmermann Zsolt",
            author_email="https://github.com/cina/idokep",
            config={
                'StdRESTFul': {
                    'IDOKEP': {
                        'username':'INSERT_USERNAME_HERE',
                        'password':'INSERT_PASSWORD_HERE'
                    }
                }
            },
            files=[('bin/user', ['bin/user/idokep.py'])]
        )
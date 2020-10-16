import os
import sys
import glob
import time
import argparse
import threading
from abc import abstractmethod

from OpenGL.GL import *
from OpenGL.GLU import *
from OpenGL.GLUT import *

import cv2
import numpy as np
import yaml

from openvino.inference_engine import IECore, StatusCode, WaitMode

inf_count = 0

canvas = np.zeros([0], dtype=np.uint8)
abort_flag = False

class FullScreenCanvas:
    def __init__(self, winname='noname', shape=(1080, 1920, 3), full_screen=True):
        global canvas
        self.shape   = shape
        self.winname = winname
        canvas  = np.zeros((self.shape), dtype=np.uint8)

    def __del__(self):
        return

    def update(self):
        return

    def getROI(self, x0, y0, x1, y1):
        global canvas
        return canvas[y0:y1, x0:x1, :]

    def setROI(self, img, x0, y0, x1, y1):
        global canvas
        canvas[y0:y1, x0:x1, :] = img
    
    def displayOcvImage(self, img, x, y):
        h,w,_ = img.shape
        self.setROI(img, x, y, x+w, y+h)



class BenchmarkCanvas(FullScreenCanvas):
    def __init__(self, display_resolution=[1920,1080], full_screen=True):
        super().__init__('Benchmark', (display_resolution[1], display_resolution[0], 3), full_screen=full_screen)
        self.disp_res = display_resolution

        # Grid area to display inference result
        self.grid_col = 10
        self.grid_row = 5
        self.grid_area = [(0,0), (int(self.shape[1]), int(self.shape[0]*3/4))]
        self.grid_width  = int((self.grid_area[1][0]-self.grid_area[0][0])/self.grid_col)
        self.grid_height = int((self.grid_area[1][1]-self.grid_area[0][1])/self.grid_row)
        self.idx = 0    # current display pane index
        self.marker_img = np.full((self.grid_height, self.grid_width, 3), (64,64,64), dtype=np.uint8)

        # Status area
        self.sts_area = [ (0, int(self.shape[0]*3/4)), (self.shape[1]-1, self.shape[0]-1) ]

        # Calculate status grid size
        self.sts_grid_size = int(self.disp_res[0] / 80)


    def calcPaneCoord(self, paneIdx):
        col =  paneIdx  % self.grid_col
        row = (paneIdx // self.grid_col) % self.grid_row
        x0 = int(col * self.grid_width  + self.grid_area[0][0])
        y0 = int(row * self.grid_height + self.grid_area[0][1])
        x1 = int(x0 + self.grid_width)
        y1 = int(y0 + self.grid_height)
        return x0, y0, x1, y1

    def displayPane(self, ocvimg, idx=-1):
        if idx == -1:
            idx = self.idx
        self.idx = idx + 1
        x0, y0, x1, y1 = self.calcPaneCoord(idx)
        x1 -= 2
        y1 -= 2
        #img = cv2.resize(ocvimg, (self.grid_width-2, self.grid_height-2))
        img = ocvimg[0:self.grid_height-2, 0:self.grid_width-2, :]
        self.setROI(img, x0, y0, x1, y1)

    def markCurrentPane(self, idx=-1):
        if idx == -1:
            idx = self.idx
        x0, y0, x1, y1 = self.calcPaneCoord(idx)
        self.setROI(self.marker_img, x0, y0, x1, y1)

    def displayLogo(self):
        global canvas
        stsY = self.grid_height * self.grid_row
        gs = self.sts_grid_size

        logo1 = os.path.join('logo', 'logo-classicblue-3000px.png')
        logo2 = os.path.join('logo', 'int-openvino-wht-3000.png')

        if os.path.isdir('logo'):
            tmpimg = cv2.imread(logo1)
            h = tmpimg.shape[0]
            tmpimg = cv2.resize(tmpimg, None, fx=(gs*4)/h, fy=(gs*4)/h)    # Logo height = 3*gs
            self.displayOcvImage(tmpimg, gs*26, stsY+gs*7)

            tmpimg = cv2.imread(logo2, cv2.IMREAD_UNCHANGED)
            b,g,r,alpha = cv2.split(tmpimg)
            tmpimg = cv2.merge([alpha,alpha,alpha])
            h = tmpimg.shape[0]
            tmpimg = cv2.resize(tmpimg, None, fx=(gs*4)/h, fy=(gs*4)/h) 
            self.displayOcvImage(tmpimg, gs*32, stsY+gs*7)
        else:
            cv2.putText(canvas, 'OpenVINO', (gs*32, stsY+gs*9), cv2.FONT_HERSHEY_PLAIN, 5, (255,255,255), 5)

    def displayModel(self, modelName, device, batch, skip_count):
        global canvas
        _, name = os.path.split(modelName)
        name,_ = os.path.splitext(name)
        stsY = self.grid_height * self.grid_row
        gs = self.sts_grid_size
        ts = self.disp_res[0] / 960         # text size
        tt = self.disp_res[0] / 960         # text thickness
        tt = int(max(tt,1))
        txt = 'model: {} ({})'.format(name, device)
        cv2.putText(canvas, txt, (gs*1, stsY+gs* 8), cv2.FONT_HERSHEY_PLAIN, ts, (255,255,255), tt)
        txt = 'batch: {}, skip frame: {}'.format(batch, skip_count)
        cv2.putText(canvas, txt, (gs*1, stsY+gs*10), cv2.FONT_HERSHEY_PLAIN, ts, (255,255,255), tt)

    # elapse = sec
    def dispProgressBar(self, curItr, ttlItr, elapse, max_fps=100):
        global canvas
        def progressBar(img, x0, y0, x1, y1, val, color):
            val = min(100, val)
            xx = int(((x1-x0)*val)/100)+x0
            cv2.rectangle(img, (x0,y0), (xx,y1), color, -1)
            cv2.rectangle(img, (xx,y0), (x1,y1), (32,32,32), -1)
        img = canvas

        stsY  = self.grid_height * self.grid_row
        gs = self.sts_grid_size             # status pane grid size (dot)
        ts = self.disp_res[0] / 960         # text size
        tt = self.disp_res[0] / 960         # text thickness
        tt = int(max(tt,1))
        # erase numbers on the right
        cv2.rectangle(img, (gs*66, stsY), (self.disp_res[0]-1, self.disp_res[1]-1), (0,0,0), -1)

        cv2.putText(img, 'Progress:', (gs* 1, stsY+gs*2), cv2.FONT_HERSHEY_PLAIN, ts, (255,255,255), tt)
        progressBar(img, gs*8, stsY+gs*1, gs*64, stsY+gs*3, (curItr*100)/ttlItr, (255,255,32))
        cv2.putText(img, '{}/{}'.format(curItr,ttlItr)          , (gs*66, stsY+gs*2), cv2.FONT_HERSHEY_PLAIN, ts, (255,255,255), tt)

        cv2.putText(img, 'FPS:'     , (gs*1, stsY+gs*5), cv2.FONT_HERSHEY_PLAIN, ts, (255,255,255), tt)
        progressBar(img, gs*8, stsY+gs*4, gs*64, stsY+gs*6, (curItr*100/elapse)/max_fps, (128,255,0))
        cv2.putText(img, '{:5.2f} inf/sec'.format(curItr/elapse), (gs*66, stsY+gs*5), cv2.FONT_HERSHEY_PLAIN, ts, (255,255,255), tt)

        cv2.putText(img, 'Time: {:5.1f}'.format(elapse), (gs*66, stsY+gs*8), cv2.FONT_HERSHEY_PLAIN, ts, (255,255,255), tt)


framebuf_lock = threading.Lock()

class benchmark():
    def __init__(self, model, device='CPU', nireq=4, config=None):
        self.config = config
        self.read_labels()
        base, ext = os.path.splitext(model)
        self.ie = IECore()
        self.net = self.ie.read_network(base+'.xml', base+'.bin')
        if 'batch' in self.config['model_config']:
            self.batch = self.config['model_config']['batch']
        else:
            self.batch = 1
        self.net.batch_size = self.batch
        self.inputBlobName  = next(iter(self.net.input_info))
        self.outputBlobName = next(iter(self.net.outputs)) 
        self.inputShape  = self.net.input_info  [self.inputBlobName ].tensor_desc.dims
        self.outputShape = self.net.outputs     [self.outputBlobName].shape

        # Setup network configuration parameters
        print('*** SET CONFIGURATION') 
        network_cfg = self.config['plugin_config']
        if device in network_cfg:
            cfg_items = network_cfg[device]
            for cfg in cfg_items:
                self.ie.set_config(cfg, device)
                print('   ', cfg, device)

        self.exenet = self.ie.load_network(self.net, device, num_requests=nireq)
        self.nireq = nireq
        self.inf_count = 0

        disp_res = [ int(i) for i in self.config['display_resolution'].split('x') ]  # [1920,1080]
        self.canvas = BenchmarkCanvas(display_resolution=disp_res, full_screen=self.config['full_screen'])
        self.skip_count = self.config['display_skip_count']
        self.canvas.displayLogo()
        self.canvas.displayModel(model, device, self.batch, self.skip_count)
        self.thread_abort_flag = False
        self.infer_slot = [ [False, 0] for req in self.exenet.requests ]   # [Inuse flag, ocvimg index]

    def read_labels(self):
        if 'label_file' in self.config['model_config']:
            label_file = self.config['model_config']['label_file']
            with open(label_file, 'rt') as f:
                self.labels = [ line.rstrip('\n').split(',')[0] for line in f ]
        else:
            self.labels = None


    def preprocessImages(self, files):
        self.blobImages = []
        self.ocvImages = []
        for f in files:
            img = cv2.imread(f)
            img = cv2.resize(img, (self.inputShape[-1], self.inputShape[-2]))
            blobimg = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            blobimg = blobimg.transpose((2,0,1))
            blobimg = blobimg.reshape(self.inputShape[1:])
            self.ocvImages.append(img)
            self.blobImages.append(blobimg)

    def run(self, niter=10, nireq=4, files=None, max_fps=100):
        global abort_flag, framebuf_lock
        print('*** CURRENT CONFIGURATION')
        met_keys = self.exenet.get_metric('SUPPORTED_METRICS')
        cfg_keys = self.exenet.get_metric('SUPPORTED_CONFIG_KEYS')
        for key in cfg_keys:
            print('   ', key, self.exenet.get_config(key))

        self.thread_abort_flag = False
        niter = (niter//self.batch)*self.batch + (self.batch if niter % self.batch else 0)  # tweak number of iteration for batch inferencing
        self.inf_count = 0

        start = time.perf_counter()
        # Do inference
        while self.inf_count < niter:
            self.exenet.wait(num_requests=1, timeout=WaitMode.RESULT_READY)
            request_id = self.exenet.get_idle_request_id()
            infreq = self.exenet.requests[request_id]

            # if slot has already been in use, process the infer result
            if self.infer_slot[request_id][0] == True:
                self.inf_count += self.batch
                ocvIdx = self.infer_slot[request_id][1]   # OCV image index
                ocvimg = self.ocvImages[ocvIdx].copy()
                res = infreq.output_blobs[self.outputBlobName].buffer[0].ravel()
                idx = (res.argsort())[::-1]
                txt = self.labels[idx[0]]
                cv2.putText(ocvimg, txt, (0, ocvimg.shape[-2]//2), cv2.FONT_HERSHEY_PLAIN, 2, (  0,  0,  0), 5 )
                cv2.putText(ocvimg, txt, (0, ocvimg.shape[-2]//2), cv2.FONT_HERSHEY_PLAIN, 2, (255,255,255), 2 )
                self.canvas.displayPane(ocvimg)
                self.infer_slot[request_id] = [False, 0]

            dataIdx = self.inf_count % len(self.blobImages)
            self.infer_slot[request_id] = [True, dataIdx]
            infreq.async_infer(inputs={ self.inputBlobName : self.blobImages[dataIdx] } )

            framebuf_lock.acquire()
            self.canvas.dispProgressBar(curItr=self.inf_count, ttlItr=niter, elapse=time.perf_counter()-start, max_fps=max_fps)
            self.canvas.markCurrentPane()
            framebuf_lock.release()

            if abort_flag == True:
                break
        end = time.perf_counter()

        if abort_flag == False:
            # Display the rsult
            print('Time: {:8.2f} sec, Throughput: {:8.2f} inf/sec'.format(end-start, niter/(end-start)))
            self.canvas.dispProgressBar(curItr=niter, ttlItr=niter, elapse=end-start, max_fps=max_fps)
            glutPostRedisplay()
            time.sleep(5)
        else:
            print('Benchmark aborted')
        abort_flag = True




def draw():
    global canvas, framebuf_lock
    framebuf_lock.acquire()
    img= cv2.cvtColor(canvas,cv2.COLOR_BGR2RGB)
    framebuf_lock.release()
    h, w = img.shape[:2]
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, w, h, 0, GL_RGB, GL_UNSIGNED_BYTE, img)

    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
    glColor3f(1.0, 1.0, 1.0)

    # Enable texture map
    glEnable(GL_TEXTURE_2D)
    # Set texture map method
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)

    # draw square
    glBegin(GL_QUADS) 
    glTexCoord2d(0.0, 1.0)
    glVertex3d(-1.0, -1.0,  0.0)
    glTexCoord2d(1.0, 1.0)
    glVertex3d( 1.0, -1.0,  0.0)
    glTexCoord2d(1.0, 0.0)
    glVertex3d( 1.0,  1.0,  0.0)
    glTexCoord2d(0.0, 0.0)
    glVertex3d(-1.0,  1.0,  0.0)
    glEnd()
    glFlush();
    glutSwapBuffers()

def init():
    glClearColor(0.0, 0.0, 0.0, 0.0)

def idle():
    global abort_flag
    if abort_flag == True:
        sys.exit(0)

def timer(val):
    global abort_flag
    if abort_flag == True:
        sys.exit(0)
    glutPostRedisplay()
    glutTimerFunc(100, timer, 0)

def reshape(w, h):
    glViewport(0, 0, w, h)
    glLoadIdentity()
    glOrtho(-w / 1920, w / 1920, -h / 1080, h / 1080, -1.0, 1.0)

def keyboard(key, x, y):
    global abort_flag
    key = key.decode('utf-8')
    if key == 'q':
        abort_flag = True



def main():
    global abort_flag

    abort_flag = False

    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', default='default.yml', type=str, help='Input configuration file (YAML)')
    args = parser.parse_args()

    # Read YAML configuration file
    with open(args.config, 'rt') as f:
        config = yaml.safe_load(f)
    #for key, val in config.items():
    #    print(key, val)

    image_src = config['image_source_dir']
    files = glob.glob(os.path.join(image_src, '*.'+config['image_data_extension']))
    if len(files)==0:
        print('ERROR: No input images are found. Please check \'image_source_dir\' setting in the YAML configuration file.')
        return 1

    model = config['xml_model_path']
    if not os.path.isfile(model):
        print('ERROR: Model file is not found. ({})'.format(model))
        return 1
    model_type = config['model_config']['type']
    bm = benchmark(model, device=config['target_device'], config=config)

    bm.preprocessImages(files)
    th = threading.Thread(target=bm.run, kwargs={
        'niter'  :config['iteration'], 
        'nireq'  :config['num_requests'], 
        'max_fps':config['fps_max_value']})
    th.start()

    glutInitWindowPosition(0, 0);
    glutInitWindowSize(1920, 1080);
    glutInit(sys.argv)
    glutInitDisplayMode(GLUT_RGBA | GLUT_DOUBLE )
    #glutCreateWindow("Display")
    glutEnterGameMode()
    glutDisplayFunc(draw)
    glutReshapeFunc(reshape)
    glutKeyboardFunc(keyboard)
    init()
    glutIdleFunc(None)
    glutTimerFunc(100, timer, 0)
    glutMainLoop()

    return 1

if __name__ == '__main__':
    sys.exit(main())
